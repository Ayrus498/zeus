// Prevents an extra console window on Windows in release; harmless on Linux.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // `zeus --cli` (also `-cli` / `cli`; the old `-code` / `--code` / `code` still work) opens the terminal UI instead of the desktop window.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if matches!(args.first().map(String::as_str), Some("--cli" | "-cli" | "cli" | "-code" | "--code" | "code")) {
        zeus_lib::run_code(&args[1..]);
    }
    // `zeus` in a terminal opens the app in its own session and gives the prompt back (`--foreground` keeps it attached).
    zeus_lib::maybe_detach(&args);
    zeus_lib::run()
}
