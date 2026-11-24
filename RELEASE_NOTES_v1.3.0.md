# Desktop Edition 1.3.0

## Interface and branding

The downloader has a navy and mint palette, custom navigation glyphs, queue summary cards, alternating table rows, colored failure states, and a useful empty state. The download action remains in the header. On compact windows, summary cards give way to the source form and queue. Long source forms, settings, and history scroll.

Source forms and profile results remain alive across navigation. Text pasted into Direct URLs survives tab changes, invalid lines remain available for correction, and queue selection survives background refreshes. Ctrl+Enter starts a download; Ctrl+A and Delete work in the queue.

New assets include the generated media/download symbol, a locally typeset PNG/SVG wordmark, and a Windows ICO containing 16, 24, 32, 48, 64, 128, and 256 pixel sizes. The app header, window, About page, and build assets use the new identity. Generation details are in `assets/BRANDING.md`.

## Review findings addressed

| Problem | Result |
| --- | --- |
| Profile discovery state was uninitialized until a successful scan. | Ordinary downloads and manifest exports work immediately after launch. |
| Firefox choices were only populated when Settings was opened. | Saved browser profile selection is initialized at startup and retained even if temporarily unavailable. |
| Switching pages destroyed widgets referenced by background workers. | The workspace persists, and workers send events through a queue handled on the Tk thread. |
| Retrying unsuccessful items discarded unrelated queued work. | Only failed/deferred assets are submitted to the retry job; other assets remain queued. |
| Active queue entries could be removed while the worker continued downloading them. | Removal and clearing are blocked during an active download. |
| JSON preferences/history could contain the wrong top-level type or be truncated during a write. | Reads validate their shape; settings/history writes replace a complete file atomically. |
| ZIP naming only checked whether the extracted folder existed. | Naming also checks existing ZIPs, and archives are written to a temporary file before replacement. |
| Thumbnail redirects were followed before checking the destination. | Every redirect is checked before requesting it, with a bounded hop count. |
| Quoted header/session mappings could evade diagnostic redaction. | JSON and Python-style quoted Cookie/Authorization headers and session values are scrubbed. |
| Overlapping thumbnail scans shared one temporary filename. | Each writer uses a unique temporary file and cleans it up. |
| HTML could be accepted as video based on a filename and header. | Recognized video signatures are required. |
| A later invalid carousel slide could leave earlier copied slides unreported. | Every slide is validated before copying begins. |
| Instagram media did not honor the configured file limit when packaged. | Oversized media fails validation before copying. This is a packaging limit; gallery-dl may already have transferred it. |
| Malformed URL ports or enum shapes raised incidental Python exceptions. | They produce reportable asset errors. Publication subpaths are rejected. |
| Windows device names could produce unusable file/folder names. | Reserved names are prefixed before use on disk. |
| UTF-8 BOM manifests were rejected, and exported profile provenance was missing from the schema. | BOM files load, and the schema describes optional profile discovery metadata. |
| The frozen-path test used an unresolved POSIX-style path on Windows. | The test uses an absolute resolved path. |
| Builds could package code without running checks. | Local and CI builds run the regression suite first; a separate CI workflow tests changes. |

## Validation and remaining limits

The baseline suite had 49 passing tests and one Windows path assertion failure. The updated suite has **78 passing tests**, including eight native Tk workflows and an end-to-end direct-media package test using an in-memory HTTP transport. Source compilation and native previews cover the default and minimum window sizes. Test/preview runs use temporary settings rather than the user's preferences or cookies.

The standalone Windows executable was rebuilt successfully. Its GUI initialized and closed with exit code 0 using isolated settings, and its bundled gallery-dl helper returned `1.32.22-dev` with exit code 0. The PowerShell build script, manifest schema JSON, SVG markup, and all seven ICO sizes were also checked.

Live Instagram authentication and remote publication downloads were not exercised. Gallery-dl still follows the existing official development archive dependency, so release builds are not fully reproducible until a tested upstream revision is pinned. Direct URL handling retains the existing DNS validation architecture; these changes are not a complete network-security audit.

The repository's application files were already untracked at the start of this review. No files were staged or committed.
