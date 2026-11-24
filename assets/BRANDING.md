# GTL AI Asset Downloader identity

- Mark: `GTL_AI_Asset_Downloader.png`, transparent RGBA master.
- Windows icon: `GTL_AI_Asset_Downloader.ico`, seven sizes from 16 to 256 pixels.
- Wordmark: `GTL_AI_logo.png`, transparent PNG, and `GTL_AI_logo.svg`, a self-contained SVG lockup with an embedded raster mark and editable text. The symbol is raster; the SVG is not an all-vector drawing.
- Primary palette: midnight `#0B1118`, panels `#121E2A`, mint `#65E2C4`, text `#EEF5F7`.
- Typography: Segoe UI / Segoe UI Semibold in the app; Segoe UI Bold in the exported wordmark. Use the transparent wordmark on a dark surface.

The symbol was generated with the built-in imagegen tool. The selected prompt was:

> Use case: logo-brand. Asset type: production application icon for GTL AI Asset Downloader, a Windows desktop media collection utility. Create ONE distinctive polished geometric symbol, centered and large in a square transparent canvas: an abstract downward arrow formed from two offset rounded media/image tiles flowing into a tray, subtly suggesting the letter G through negative space. Deep midnight navy rounded-square app tile, luminous mint/teal primary mark, tiny icy white accent. Sophisticated restrained precision, flat vector-like shapes with a subtle tonal teal gradient only, balanced generous margins. Legible at 24 pixels. No text, no letters, no watermark, no presentation board, no multiple versions, no surrounding props. The rounded-square tile is opaque navy, the area outside the tile genuinely transparent. Crisp edges, strong recognizable silhouette.

The final wordmark is typeset locally for exact lettering. Run `python scripts/build_brand_assets.py` on Windows to reproduce it and the ICO from the source mark. `--font-dir` can point to another directory containing `segoeuib.ttf` and `segoeui.ttf`. The SVG uses the viewer's available Segoe UI/Arial fonts; use the PNG when exact typography is required.
