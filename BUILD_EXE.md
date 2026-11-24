# Build the standalone Asset Downloader.exe

The end-user app is **Asset Downloader.exe**. It opens the complete GUI and includes the GTL AI icon.

Version 1.3.0 includes the refreshed workspace, new logo, and new icon. Each build route runs the regression suite before packaging. The PowerShell builder also accepts `-NonInteractive` and returns a nonzero exit code on failure.

To compile it once:

1. Extract the project ZIP on Windows 10/11 x64.
2. Double-click `build_windows.bat`.
3. If Python 3.11 is missing, the builder attempts to install it automatically using `winget`.
4. Wait for the build. The console stays open and reports errors.
5. The generated application is `dist\Asset Downloader.exe`.

If `winget` is unavailable, install Python 3.11 x64 from python.org and rerun the builder.

If the BAT appears to close unexpectedly, run it from Command Prompt:
- Open the extracted project folder in File Explorer.
- Click the address bar, type `cmd`, and press Enter.
- Run `build_windows.bat`.
- If it fails, send `build.log`.

The EXE is built in one-file/windowed mode with PyInstaller. It is not a launcher for Python and does not need the source folder after compilation.


## v0.2.4 build fix

The PyInstaller icon path is now absolute in all build routes:
- BAT: `%CD%\assets\GTL_AI_Asset_Downloader.ico`
- PowerShell: `$PSScriptRoot\assets\GTL_AI_Asset_Downloader.ico`
- GitHub Actions: `$GITHUB_WORKSPACE\assets\GTL_AI_Asset_Downloader.ico`

This prevents PyInstaller from resolving the relative icon path under the `build` work directory (the previous failure: `build\assets\GTL_AI_Asset_Downloader.ico`).


## v0.3.6
The v0.3.6 package adds sequential Instagram cooldowns, one conservative retry for transient errors, Retry-After handling, a circuit breaker, per-attempt diagnostics, and a **Retry failed / deferred** queue action. The Windows executable must be rebuilt from this source package; an EXE from an earlier release will not contain these changes.


## v1.1.0 — Profile thumbnail previews
The v1.1.0 source adds thumbnail extraction, background preview downloads, local cache and a click-to-open preview window in Profile Extractor. Rebuild `Asset Downloader.exe` from this package; an older EXE does not include the feature. Pillow and HTTPX are already listed in `requirements.txt`.


## v1.2.0 — Workflow and visibility improvements
The v1.2.0 release moves **Download Assets** into the persistent top action bar, adds profile-scan and thumbnail progress feedback, defaults output to an `output` folder beside the running app, and automatically removes successfully completed assets from the queue while retaining failed/deferred items. Rebuild the Windows EXE from this package to receive these changes.
