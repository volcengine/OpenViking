export function setExitCodeOnFailure(result: { success: boolean }): void {
  if (!result.success) {
    process.exitCode = 1;
  }
}
