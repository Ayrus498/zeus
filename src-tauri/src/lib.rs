//! ZEUS desktop shell.
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

/// Locate the `zeus-code` launcher: $ZEUS_CODE_BIN, next to this executable (/usr/bin/zeus-code), the source checkout
/// (dev builds), then PATH.
fn code_launcher() -> PathBuf {
    if let Ok(p) = std::env::var("ZEUS_CODE_BIN") {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sib = dir.join("zeus-code");
            if sib.exists() {
                return sib;
            }
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../packaging/zeus-code");
    if dev.exists() {
        return dev;
    }
    PathBuf::from("zeus-code")
}

/// `zeus -code [args]`: hand the terminal over to the Python terminal UI (no window is created).
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
    eprintln!("zeus -code is only available on Linux and macOS.");
    std::process::exit(1);
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let child = spawn_backend(app.handle());
            app.manage(Backend(Mutex::new(child)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building ZEUS")
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
