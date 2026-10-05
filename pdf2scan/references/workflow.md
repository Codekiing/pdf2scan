# Atomic workflow

The implementation is `../scripts/scan_to_pdf.py`. Image arrays are OpenCV BGR `uint8` unless the node says grayscale. PDF and photograph inputs converge after page preparation; only photographs receive geometry and illumination correction.

| Step | Input → output | Operation and guard |
| --- | --- | --- |
| 1. Capture | PDF page or image file → upright BGR pixels + source kind | Render each PDF page with `pdftoppm` at `--dpi` (PyMuPDF fallback), or decode an image with EXIF orientation. PDF pages keep the rendered frame. |
| 2. Find corners | Photograph BGR → zero, one, or two quadrilaterals | `--geometry none` means already cropped (zero). A supplied `--quads` gives one exact page or two approximate spread pages. Otherwise detect one sheet automatically. Stop if detection is uncertain; never silently fall back to the full photograph. |
| 3. Refine and rectify | Photograph + quadrilaterals → rectangular BGR page(s) | Refine automatic corners and spread edges, align shared spine endpoints, then use a four-point homography on each page and its printed content together. A single manual quad is exact. Deskew only when multiple reliable printed rules agree within `--deskew-max-degrees`. PDFs and pre-cropped images skip this step. |
| 4. Compose and size | Rectified BGR page(s) → one bounded BGR raster | Stitch two pages left to right with no inserted gutter. Default spread width/height is 1.35 unless measured `--aspect` or `--no-auto-spread-aspect` applies. Cap the longest side with `--max-dimension`. PDF pages retain their capture proportion. |
| 5. Tone and color | BGR raster + source kind + color mode → BGR or gray scan raster | Rendered PDFs use their rendered-page tone curve without the photograph shadow estimator. Photographs estimate broad illumination, lift shaded paper, and protect dark ink and printed colors. `preserve` restores color by default; explicit `grayscale` yields gray. The white point, black point, and contrast controls apply to this stage. |
| 6. Finish ink | Toned raster → sharpened BGR or gray page | Sharpen existing strokes and set neutral white/black endpoints without binarizing antialiased edges or colored print. |
| 7. Rotate and composite | Finished page + photo backdrop → BGR page image | Rotate page, print, and mask together by a random slope within ±`--tilt-percent` (default 0.25%). Fit the supplied or bundled shadow photo beneath exposed areas. Keep the upper-left crop corner and trim the right and bottom; do not expand or shrink the paper. `--seed` fixes the random sequence. Grayscale mode desaturates the backdrop. |
| 8. Package | One or more BGR page images → raster PDF | Encode each page losslessly as PNG, then insert it at `--dpi`. The resulting PDF does not retain interactive links or selectable text. |
| 9. Review | PDF + source image → accepted artifact or corrected input | Render representative output pages and verify boundaries, text, print colors or grayscale, shadow transitions, and page count. Correct only the observed failing input or control and rerun. |

The geometry, tone, and final tilt stages are separate: refining corners does not alter print independent of its page, local shadow correction does not move geometry, and final random tilt rotates the complete finished page.
