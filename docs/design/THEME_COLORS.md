# Terminal theme colors

Use `/theme` to preview the 73 palettes. Arrow keys preview immediately, Enter saves, and Escape restores the previous theme. A theme can also be selected directly, for example `/theme matcha`.

## Color roles

| Element | Treatment |
| --- | --- |
| Menu titles and important headings | Bold accent |
| Normal labels, entered values, and ordinary text | Explicit primary ink |
| Descriptions | Muted ink |
| Hints, counts, and secondary metadata | Quieter readable ink |
| Section names and filter labels | Secondary accent |
| Selected menu row | Full-width selection fill, contrasting ink, and an arrow |
| Success, warnings, and errors | Dedicated status colors with existing text/icon cues |
| Menus, forms, authorization dialogs, and output viewers | Explicit filled surfaces, including padding |

Foreground roles are adjusted for at least 4.5:1 contrast against the canvas, menu surface, and derived card fills. Selected rows use separately checked ink. Terminal color quantization and individual terminal rendering can affect the displayed result.

## New palettes

| Theme | Character |
| --- | --- |
| `matcha` | Light botanical green, moss selections, plum details |
| `porcelain` | Light ivory, cobalt selections, copper details |
| `sakura` | Light cherry paper, mulberry selections, jade details |
| `sandstone` | Warm parchment, sienna selections, mineral blue details |
| `tidal` | Light sea glass, deep teal selections, coral details |
| `lilac` | Pale lavender, iris selections, rose details |
| `marigold` | Golden paper, umber selections, indigo details |
| `nocturne` | Midnight blue, moonlit mint selections, smoky violet details |
| `espresso` | Coffee brown, crema selections, rosewood details |
| `velvet` | Aubergine, jewel pink selections, champagne details |
| `deep-sea` | Ocean ink, luminous cyan selections, coral details |
| `copper` | Deep mineral green, oxidized teal selections, copper details |
| `oceanic` | Deep sea ink, teal selections, coral details |
| `midnight-rose` | Deep plum, blush selections, periwinkle details |
| `citrus` | Sunlit paper, olive selections, tangerine details |
| `lavender-mist` | Pale lilac, iris selections, berry details |
| `terracotta` | Warm clay, slate selections, garden green details |
| `moss` | Forest shadow, lichen selections, orchid details |
| `arctic` | Glacier paper, frozen blue selections, indigo details |
| `obsidian` | Graphite black, ice-blue selections, violet details |
| `vaporwave` | Ultraviolet, neon pink selections, aqua details |
| `clay` | Stoneware, slate selections, moss details |
| `midnight` | Navy depth, electric blue selections, lavender details |
| `sorbet` | Soft cream, raspberry selections, grape details |
| `pine` | Evergreen, mint selections, amethyst details |
| `cinder` | Charred neutral, ember selections, violet smoke |
| `honey` | Amber paper, umber selections, indigo details |
| `iris` | Violet dusk, silver-blue selections, blush details |
| `basalt` | Volcanic grey, steel-blue selections, mauve details |
| `coral-reef` | Ocean ink, coral selections, lagoon details |
| `wisteria` | Pale lilac, violet selections, rose and pine details |
| `graphite` | Cool graphite, slate-blue selections, dusty rose details |

The canonical registry is `ui/src/design/theme.ts`. Reusable overlays use `surfaceLine()` so nested ANSI resets restore the theme's ink and surface instead of reverting to the terminal profile.
