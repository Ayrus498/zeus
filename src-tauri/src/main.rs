// Prevents an extra console window on Windows in release; harmless on Linux.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // `zeus -code` (also `--code` / `code`) opens the terminal UI instead of the desktop window.
    let args: Vec<String> = std::env::args().skip(1).collect();
    if matches!(args.first().map(String::as_str), Some("-code" | "--code" | "code")) {
        zeus_lib::run_code(&args[1..]);
    }
    zeus_lib::run()
}
