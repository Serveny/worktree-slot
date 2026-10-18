#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: slot.sh claim <task-slug> [branch] | finish [--no-squash] [slot] [message] | discard [slot] | list

claim   Reuse a clean, integrated slot or create a new one from local main.
        Default branch: feat/<task-slug>. Existing branches are never overwritten.
        Prints only the absolute slot path to stdout; diagnostics go to stderr.
finish  Merge a feature branch into main (squashed by default, or with --no-squash),
        commit, and free the slot.
discard Discard changes in a slot, delete its branch, and free the slot.
list    Show slot availability, branch, task and start time.
USAGE
}

fail() {
  echo "refusing: $*" >&2
  exit 1
}

orig_pwd="$(pwd -P)"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
common_dir="$(git rev-parse --path-format=absolute --git-common-dir)"
cd -- "$common_dir/.."
root="$PWD"
mkdir -p .worktrees
shopt -s nullglob

run_hooks() {
  local event="$1" hook
  for hook in "$script_dir/hooks/$event/"*; do
    [ -f "$hook" ] && [ -x "$hook" ] || continue
    if ! "$hook" "$d" "$branch" >&2; then
      echo "warning: $event hook failed: $hook" >&2
      [ "$event" != post-claim ] || return 1
    fi
  done
  return 0
}

slot_dirs() {
  local d
  for d in "$root"/.worktrees/slot-*; do
    [[ "${d##*/}" =~ ^slot-[1-9][0-9]*$ ]] && [ -d "$d" ] && printf '%s\n' "$d"
  done
  return 0
}

resolve_slot() {
  local slot="$1" d
  case "$slot" in
    */*) d="$(realpath -m -- "$slot")" ;;
    [0-9]*) d="$root/.worktrees/slot-$slot" ;;
    *) d="$root/.worktrees/$slot" ;;
  esac
  [[ "${d%/*}" = "$root/.worktrees" && "${d##*/}" =~ ^slot-[1-9][0-9]*$ ]] || return 1
  [ -d "$d" ] || return 1
  printf '%s\n' "$d"
}

find_current_slot() {
  local cur="$orig_pwd"
  while [ -n "$cur" ] && [ "$cur" != "/" ]; do
    if [[ "${cur%/*}" = "$root/.worktrees" && "${cur##*/}" =~ ^slot-[1-9][0-9]*$ ]]; then
      [ -d "$cur" ] && printf '%s\n' "$cur" && return 0
    fi
    cur="${cur%/*}"
  done
  return 1
}

check_slot() {
  local d="$1"
  [ "$(git -C "$d" rev-parse --show-toplevel)" = "$d" ] || fail "$d is not a worktree root"
  [ "$(git -C "$d" rev-parse --path-format=absolute --git-common-dir)" = "$common_dir" ] || fail "$d belongs to another repository"
  [ "$(git -C "$d" symbolic-ref --quiet --short HEAD || true)" != main ] || fail "$d has main checked out"
}

patch_ids() {
  git -C "$1" log --format='commit %H' --no-merges --no-ext-diff --no-textconv --no-renames --binary --full-index --color=never -p "$2" |
    git patch-id --verbatim
}

is_integrated() {
  local d="$1" base upstream pending patch commit count=0
  local -A integrated_patches=()
  reason="$d is not integrated into main"
  [ -z "$(git -C "$d" status --porcelain --untracked-files=all)" ] || {
    reason="$d has uncommitted changes; preserve and finish them before reuse"
    return 1
  }
  if git -C "$d" merge-base --is-ancestor HEAD main 2>/dev/null; then
    return 0
  fi
  [ -z "$(git -C "$d" rev-list --merges main..HEAD 2>/dev/null)" ] || {
    reason="$d has merge commits outside main; review and merge its branch first"
    return 1
  }
  base="$(git -C "$d" merge-base HEAD main 2>/dev/null)" || {
    reason="$d has no merge base with main; review and merge its branch first"
    return 1
  }
  upstream="$(patch_ids "$d" "$base..main")"
  while read -r patch commit; do
    [ -n "$patch" ] || continue
    integrated_patches["$patch"]=1
  done <<< "$upstream"

  local squash_patch
  squash_patch="$(git -C "$d" diff "$base" HEAD | git patch-id --verbatim | awk '{print $1}')"
  if [ -n "$squash_patch" ] && [ -n "${integrated_patches[$squash_patch]:-}" ]; then
    return 0
  fi

  pending="$(patch_ids "$d" main..HEAD)"
  while read -r patch commit; do
    [ -n "$patch" ] || continue
    [ -n "${integrated_patches[$patch]:-}" ] || {
      reason="$d has commits not merged or cherry-picked into main: $commit"
      return 1
    }
    count=$((count + 1))
  done <<< "$pending"
  [ "$count" -eq "$(git -C "$d" rev-list --count main..HEAD 2>/dev/null)" ] || {
    reason="$d has commits without comparable patches; review and merge its branch first"
    return 1
  }
  return 0
}

merge_conflict() {
  git -C "$root" merge --abort 2>/dev/null || git -C "$root" reset --hard HEAD >/dev/null 2>&1
  fail "merge conflict between $branch and main. Resolve conflicts in $d and complete the merge into main."
}

cmd="${1:-}"
shift || true
case "$cmd" in
  claim)
    [ "$#" -ge 1 ] && [ "$#" -le 2 ] || fail "usage: slot.sh claim <task-slug> [branch]"
    slug="$1"
    [[ "$slug" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || fail "task slug must use lowercase letters, digits and single hyphens"
    branch="${2:-feat/$slug}"
    git check-ref-format --branch "$branch" >/dev/null || fail "invalid branch name: $branch"
    [[ "$branch" != main && "$branch" != slot-* && "$branch" != @* ]] || fail "choose a feature branch name: $branch"
    exec 9>"$root/.worktrees/.claim.lock"
    flock 9
    git show-ref --verify --quiet refs/heads/main || fail "local main branch is missing"
    if git show-ref --verify --quiet "refs/heads/$branch"; then
      fail "branch $branch already exists; use list to find ongoing work or choose another name"
    fi
    d=""
    n=0
    while IFS= read -r candidate; do
      number="${candidate##*-}"
      if (( number > n )); then n="$number"; fi
      if [ -z "$d" ] && [ ! -e "$candidate/.task" ]; then
        check_slot "$candidate"
        is_integrated "$candidate" || fail "$reason"
        d="$candidate"
      fi
    done < <(slot_dirs)
    if [ -n "$d" ]; then
      echo "claiming $d on $branch" >&2
      git -C "$d" switch --no-overwrite-ignore -c "$branch" main >&2
    else
      d="$root/.worktrees/slot-$((n + 1))"
      echo "creating $d on $branch (build cache starts cold)" >&2
      git worktree add -b "$branch" "$d" main >&2
    fi
    printf '%s %s\n' "$slug" "$(date -Iseconds)" > "$d/.task"
    run_hooks post-claim || fail "post-claim hook failed; $d remains claimed on $branch. Fix the failed hook and run it and the remaining hooks for this slot."
    printf '%s\n' "$d"
    ;;
  finish)
    squash=true
    raw_args=()
    for arg in "$@"; do
      case "$arg" in
        --no-squash) squash=false ;;
        --*) fail "unknown option: $arg" ;;
        *) raw_args+=("$arg") ;;
      esac
    done

    if [ "${#raw_args[@]}" -gt 0 ] && d="$(resolve_slot "${raw_args[0]}" 2>/dev/null)"; then
      msg="${raw_args[*]:1}"
    else
      d="$(find_current_slot)" || fail "usage: slot.sh finish [--no-squash] [slot] [message] (or run inside a slot directory)"
      msg="${raw_args[*]}"
    fi

    exec 9>"$root/.worktrees/.claim.lock"
    flock 9
    [ -f "$d/.task" ] || fail "$d has no .task marker"
    check_slot "$d"

    [ -z "$(git -C "$d" status --porcelain --untracked-files=all)" ] || fail "$d has uncommitted changes; commit or stash them before finish"
    [ -z "$(git -C "$root" status --porcelain --untracked-files=all)" ] || fail "main checkout at $root has uncommitted changes"
    [ "$(git -C "$root" symbolic-ref --quiet --short HEAD 2>/dev/null || true)" = "main" ] || fail "main checkout is not on branch main"

    branch="$(git -C "$d" symbolic-ref --quiet --short HEAD || true)"
    [ -n "$branch" ] || fail "$d is not on a branch"

    if is_integrated "$d"; then
      echo "branch $branch is already integrated into main" >&2
    else
      rev_count="$(git -C "$d" rev-list --count main.."$branch")"
      [ "$rev_count" -gt 0 ] || fail "no commits on $branch ahead of main"

      if [ "$squash" = true ]; then
        if [ -z "$msg" ]; then
          if [ "$rev_count" -eq 1 ]; then
            msg="$(git -C "$d" log -1 --format=%B "$branch")"
          else
            commit_summary="$(git -C "$d" log --reverse --format='* %s' main.."$branch")"
            first_subject="${commit_summary%%$'\n'*}"
            msg="${first_subject#\* }"$'\n\n'"Squashed commits:"$'\n'"${commit_summary}"
          fi
        fi

        echo "squashing $branch into main" >&2
        git -C "$root" merge --squash "$branch" >&2 || merge_conflict

        if git -C "$root" diff --cached --quiet; then
          git -C "$root" reset --hard HEAD >/dev/null 2>&1
          fail "no changes to commit from $branch into main"
        fi

        git -C "$root" commit -m "$msg" >&2
      else
        echo "merging $branch into main" >&2
        git -C "$root" merge --no-ff -m "${msg:-Merge branch '$branch' into main}" "$branch" >&2 || merge_conflict
      fi
    fi

    git -C "$d" switch --no-overwrite-ignore --detach main >&2
    rm -f -- "$d/.task"
    echo "released $d"

    run_hooks post-finish
    ;;
  discard)
    [ "$#" -le 1 ] || fail "usage: slot.sh discard [slot-path-or-N]"
    if [ "$#" -eq 1 ]; then
      d="$(resolve_slot "$1")" || fail "not a slot path: $1"
    else
      d="$(find_current_slot)" || fail "usage: slot.sh discard <slot-path-or-N> (or run inside a slot directory)"
    fi
    exec 9>"$root/.worktrees/.claim.lock"
    flock 9
    [ -f "$d/.task" ] || fail "$d has no .task marker"
    check_slot "$d"

    branch="$(git -C "$d" symbolic-ref --quiet --short HEAD || true)"

    git -C "$d" reset --hard HEAD >&2
    git -C "$d" clean -fd >&2
    git -C "$d" switch --no-overwrite-ignore --detach main >&2
    rm -f -- "$d/.task"

    if [ -n "$branch" ]; then
      git -C "$root" branch -D "$branch" >&2 || true
      echo "discarded and released $d (deleted branch $branch)"
    else
      echo "discarded and released $d"
    fi
    ;;
  list)
    total=0
    busy=0
    while IFS= read -r d; do
      total=$((total + 1))
      branch="$(git -C "$d" symbolic-ref --quiet --short HEAD || echo detached)"
      if [ -f "$d/.task" ]; then
        busy=$((busy + 1))
        slug=""; ts=""
        read -r slug ts _ < "$d/.task" || true
        echo "busy: $d  branch=$branch  task=$slug  since=$ts"
      else
        echo "free: $d  branch=$branch"
      fi
    done < <(slot_dirs)
    echo "slots: total=$total  busy=$busy"
    ;;
  *)
    usage >&2
    exit 1
    ;;
esac
