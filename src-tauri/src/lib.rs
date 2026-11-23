//! Zeus-Agent desktop shell.
//!
//! The shell is intentionally thin: it owns the window and the lifecycle of the Python
//! backend process. All file access, permissions, and Ollama traffic live in the backend.

use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::{Manager, RunEvent};

const BACKEND_ADDR: &str = "127.0.0.1:8765";

struct Backend(Mutex<Option<Child>>);

fn backend_listening() -> bool {
    let addr: SocketAddr = BACKEND_ADDR.parse().expect("valid address");
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

/// Locate (python, working dir) for the backend.
/// - dev builds: the repo's backend/.venv
/// - installed .deb: the venv created by the package's postinst under the resource dir
fn backend_command(app: &tauri::AppHandle) -> Option<(PathBuf, PathBuf)> {
    if cfg!(debug_assertions) {
        let backend = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../backend");
        let py = backend.join(".venv/bin/python");
        return Some((
            if py.exists() {
                py
            } else {
                PathBuf::from("python3")
            },
            backend,
        ));
    }
    let res = app.path().resource_dir().ok()?;
    let py = res.join("venv/bin/python");
    Some((
        if py.exists() {
            py
        } else {
            PathBuf::from("python3")
        },
        res.join("backend"),
    ))
}

fn spawn_backend(app: &tauri::AppHandle) -> Option<Child> {
    if backend_listening() {
        return None; // already running (e.g. started by `scripts/run-backend.sh`)
    }
    let (python, cwd) = backend_command(app)?;
    match Command::new(&python)
        .args(["-m", "app"])
        .current_dir(&cwd)
        .stdin(Stdio::null())
        .spawn()
    {
        Ok(child) => {
            // Wait briefly so the UI's first request does not race the server start-up.
            for _ in 0..50 {
                if backend_listening() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
            Some(child)
        }
        Err(e) => {
            eprintln!("zeus: failed to start backend ({python:?} in {cwd:?}): {e}");
            None
        }
    }
}

/// Locate the `zeus-cli` launcher: $ZEUS_CLI_BIN (or the old $ZEUS_CODE_BIN), next to this executable (/usr/bin/zeus-cli), the source checkout
/// (dev builds), then PATH.
fn code_launcher() -> PathBuf {
    if let Ok(p) = std::env::var("ZEUS_CLI_BIN").or_else(|_| std::env::var("ZEUS_CODE_BIN")) {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sib = dir.join("zeus-cli");
            if sib.exists() {
                return sib;
            }
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../packaging/zeus-cli");
    if dev.exists() {
        return dev;
    }
    PathBuf::from("zeus-cli")
}

/// `zeus --cli [args]`: hand the terminal over to the Python terminal UI (no window is created).
#[cfg(unix)]
pub fn run_code(args: &[String]) -> ! {
    use std::os::unix::process::CommandExt;
    let launcher = code_launcher();
    let err = Command::new(&launcher).args(args).exec(); // only returns on failure
    eprintln!("zeus: cannot start the terminal UI ({launcher:?}): {err}");
    std::process::exit(1);
}

#[cfg(not(unix))]
pub fn run_code(_args: &[String]) -> ! {
    eprintln!("zeus --cli is only available on Linux and macOS.");
    std::process::exit(1);
}

/// Should `zeus` hand itself over to a detached copy and give the terminal back?
/// Yes when it was started from a terminal, unless it already is that detached copy, the user asked to stay attached
/// (`--foreground`, `--no-detach` or ZEUS_FOREGROUND), or this is a debug build (`cargo tauri dev` needs the process to stay).
pub fn detach_wanted(args: &[String], already_detached: bool, foreground_env: bool, has_terminal: bool, debug_build: bool) -> bool {
    let stay = args.iter().any(|a| a == "--foreground" || a == "--no-detach");
    has_terminal && !already_detached && !foreground_env && !stay && !debug_build
}

/// Start a copy of this program in its own session (no controlling terminal, output appended to a log file) and return
/// its pid. Closing the terminal, Ctrl+C or a hang-up then cannot reach the app.
#[cfg(unix)]
fn spawn_detached(args: &[String]) -> std::io::Result<u32> {
    use std::fs::OpenOptions;
    use std::os::unix::process::CommandExt;
    extern "C" {
        fn setsid() -> i32;
    }
    let exe = std::env::current_exe()?;
    let rest: Vec<&String> = args.iter().filter(|a| *a != "--foreground" && *a != "--no-detach").collect();
    let log = std::env::var("ZEUS_DATA_DIR")
        .map(PathBuf::from)
        .or_else(|_| std::env::var("HOME").map(|h| PathBuf::from(h).join(".local/share/zeus")))
        .ok()
        .and_then(|dir| {
            std::fs::create_dir_all(&dir).ok()?;
            OpenOptions::new().create(true).append(true).open(dir.join("zeus.log")).ok()
        });
    let mut cmd = Command::new(exe);
    cmd.args(rest).env("ZEUS_DETACHED", "1").stdin(Stdio::null());
    match log {
        Some(f) => {
            let g = f.try_clone()?;
            cmd.stdout(Stdio::from(f)).stderr(Stdio::from(g));
        }
        None => {
            cmd.stdout(Stdio::null()).stderr(Stdio::null());
        }
    }
    // SAFETY: setsid only changes this child's session, between fork and exec.
    unsafe {
        cmd.pre_exec(|| {
            setsid();
            Ok(())
        });
    }
    Ok(cmd.spawn()?.id())
}

/// `zeus` typed in a terminal: open the app in the background and return the prompt. Falls back to running in the
/// foreground when the detached copy cannot be started.
#[cfg(unix)]
pub fn maybe_detach(args: &[String]) {
    use std::io::IsTerminal;
    let has_terminal = std::io::stdin().is_terminal() || std::io::stdout().is_terminal() || std::io::stderr().is_terminal();
    let wanted = detach_wanted(args, std::env::var_os("ZEUS_DETACHED").is_some(), std::env::var_os("ZEUS_FOREGROUND").is_some(), has_terminal, cfg!(debug_assertions));
    if !wanted {
        return;
    }
    match spawn_detached(args) {
        Ok(pid) => {
            println!("Zeus-Agent started (pid {pid}); the terminal is free. Add --foreground to keep it attached.");
            std::process::exit(0);
        }
        Err(e) => eprintln!("zeus: could not start in the background ({e}); running in this terminal"),
    }
}

#[cfg(not(unix))]
pub fn maybe_detach(_args: &[String]) {}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let child = spawn_backend(app.handle());
            app.manage(Backend(Mutex::new(child)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Zeus-Agent")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<Backend>() {
                    if let Some(mut child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::detach_wanted;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn detaches_only_from_a_terminal_in_a_release_build() {
        assert!(detach_wanted(&a(&[]), false, false, true, false));
        assert!(!detach_wanted(&a(&[]), false, false, false, false), "desktop launcher has no terminal");
        assert!(!detach_wanted(&a(&[]), true, false, true, false), "already the detached copy");
        assert!(!detach_wanted(&a(&[]), false, true, true, false), "ZEUS_FOREGROUND");
        assert!(!detach_wanted(&a(&["--foreground"]), false, false, true, false));
        assert!(!detach_wanted(&a(&["--no-detach"]), false, false, true, false));
        assert!(!detach_wanted(&a(&[]), false, false, true, true), "debug builds stay attached");
    }
}
