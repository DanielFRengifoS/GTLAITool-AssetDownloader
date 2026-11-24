# GTL AI Asset Downloader v0.3.2

## Instagram profile discovery dependency correction

The supplied diagnostic log showed:

`KeyError: 'PolarisProfilePageContentQuery'`

The failure occurs inside gallery-dl while resolving Instagram profile/user metadata, before publication enumeration. The app's shortcode parser is not the cause.

Changes:
- Raise the minimum gallery-dl version to 1.32.21, whose release notes include an Instagram change to use profile data as fallback for user information.
- Restrict the supported dependency line to gallery-dl >=1.32.21,<1.33 for reproducible compatibility.
- Change Windows BAT and PowerShell builders to run `pip install --upgrade -r requirements.txt`, so a pre-existing `.venv` cannot silently retain an older satisfying version.
- Record `pip show gallery-dl` in the build log.
- Query the exact bundled gallery-dl version at profile-scan time and include it in diagnostics.
- Refuse to scan if the bundled version is older than 1.32.21.
- Redact transient `lsd`, `fb_dtsg`, CSRF and session token values from saved diagnostics.

## Evidence and limitations

The upstream v1.32.21 changelog lists "Use profile data as fallback for user info in instagram". This is directly relevant to the failure in the uploaded log, but the exact profile scan has not yet been verified against the user's live Instagram session.

Validation:
- Python syntax compilation: passed.
- Unit tests: 14/14 passed.
- Windows EXE build: pending.
- Live Instagram discovery: pending.
