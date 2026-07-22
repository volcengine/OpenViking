#!/usr/bin/env bash

# Shared --auto-commit support for benchmark train/eval launchers.

openviking_capture_train_launch_command() {
  local -a command=(bash "$0")
  local argument
  while [[ $# -gt 0 ]]; do
    argument="$1"
    case "${argument}" in
      --api-key|--access-token|--auth-token|--password|--secret|--token)
        command+=("${argument}" "***")
        shift
        if [[ $# -gt 0 ]]; then
          shift
        fi
        ;;
      --api-key=*|--access-token=*|--auth-token=*|--password=*|--secret=*|--token=*)
        command+=("${argument%%=*}=***")
        shift
        ;;
      *)
        command+=("${argument}")
        shift
        ;;
    esac
  done

  printf -v OPENVIKING_TRAIN_LAUNCH_COMMAND '%q ' "${command[@]}"
  OPENVIKING_TRAIN_LAUNCH_COMMAND="${OPENVIKING_TRAIN_LAUNCH_COMMAND% }"
  export OPENVIKING_TRAIN_LAUNCH_COMMAND
}

openviking_train_auto_commit() {
  local repo_root="$1"
  local run_name="$2"
  if [[ "${AUTO_COMMIT:-false}" != "true" ]]; then
    unset OPENVIKING_TRAIN_GIT_NOTES_COMMIT
    unset OPENVIKING_TRAIN_LAUNCH_COMMAND
    return 0
  fi

  if [[ -n "$(git -C "${repo_root}" status --porcelain)" ]]; then
    local commit_message="auto-commit before ${run_name} $(date +%Y%m%d_%H%M%S)"
    printf '[auto-commit] detected pending changes, committing...\n'
    git -C "${repo_root}" add -A
    git -C "${repo_root}" commit -m "${commit_message}"
  else
    printf '[auto-commit] working tree clean, nothing to commit\n'
  fi

  OPENVIKING_TRAIN_GIT_NOTES_COMMIT="$(git -C "${repo_root}" rev-parse HEAD)"
  export OPENVIKING_TRAIN_GIT_NOTES_COMMIT
  printf '[auto-commit] current commit: %s\n' \
    "$(git -C "${repo_root}" rev-parse --short HEAD)"
}
