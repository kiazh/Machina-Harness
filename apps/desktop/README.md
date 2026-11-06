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

The desktop app starts the Machina Python gateway directly. It never launches,
embeds, or depends on the terminal UI package. The CLI remains installed through
the PowerShell/curl installers; desktop releases are native Windows/macOS packages.
For now, a desktop release uses the installed Machina Python engine on PATH. A
fully managed embedded engine is a separate distribution milestone, not a hidden
dependency on the terminal client.

Build a native installer package (not the CLI) with:

```sh
npm run desktop:bundle
```

Checks:

```sh
npm run desktop:check
npx playwright test --config apps/desktop/playwright.config.ts
```

The browser fixtures are synthetic and excluded from the production entry.
Windows native engine initialization, file reading and Pyright completion have
been exercised. Native macOS acceptance remains outstanding; browser checks do
not establish macOS behavior.
