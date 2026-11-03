# Machina Desktop engineering preview

Agent conversation is the main workspace. Files open in an editable companion
pane; hiding that pane retains buffers and undo history. Nine appearance presets include Porcelain, Ink, Volt, Afterimage and Chrome. Appearance settings
control custom wallpaper, glass, opacity, accent, fonts and motion. Wallpaper stays
in the local WebView database; it is not uploaded to a provider.

From the repository root, after creating the normal Python `.venv`:

```sh
npm ci --ignore-scripts
npm run desktop:dev
```

Native compilation needs Rust and the platform build tools (Microsoft C++ tools
on Windows, Xcode command line tools on macOS). The launcher finds `.venv` and
Rust without changing your global PATH. Override `MACHINA_PYTHON` if needed.
The existing PostgreSQL engine profile remains required.

To build and run the optimized development-engine client:

```sh
npm run desktop:native
npm run desktop:preview
```

This client explicitly uses the development Python checkout. It is not a portable
installer or a self-contained engine distribution. Release signing, managed engine
packaging and updates are later gates in the desktop roadmap. The terminal client
remains available while desktop feature parity is completed.

Checks:

```sh
npm run desktop:check
npx playwright test --config apps/desktop/playwright.config.ts
```

The browser fixtures are synthetic and excluded from the production entry.
Windows native engine initialization, file reading and Pyright completion have
been exercised. Native macOS acceptance remains outstanding; browser checks do
not establish macOS behavior.
