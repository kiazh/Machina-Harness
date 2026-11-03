mod engine;
mod terminal;
use tauri::Manager;

pub fn run() {
    tauri::Builder::default()
        .manage(engine::EngineHost::default())
        .manage(terminal::TerminalHost::default())
        .invoke_handler(tauri::generate_handler![engine::engine_probe, engine::engine_start, engine::engine_request, engine::engine_stop,
            terminal::terminal_open, terminal::terminal_read, terminal::terminal_write, terminal::terminal_resize, terminal::terminal_close])
        .build(tauri::generate_context!())
        .expect("could not start Machina Desktop")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::Exit) { app.state::<terminal::TerminalHost>().close_all(); }
        });
}
