//! DevColleague 桌面外殼：啟動並管理 Python 後端子行程。
//!
//! 啟動流程：
//! 1. 取得一個可用的動態 port、產生隨機 session token
//! 2. 啟動後端（開發模式使用 backend/.venv 的 Python；發佈版使用 sidecar 執行檔）
//!    - port 以命令列參數傳入；token 以環境變數傳入，不出現在命令列
//!    - 後端的 stdin 接到本行程，本行程結束（包含異常結束）時後端會自動退出
//! 3. 前端透過 `backend_info` 指令取得 port 與 token
//! 4. App 結束時結束後端子行程

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

const SESSION_TOKEN_ENV: &str = "DEVCOLLEAGUE_SESSION_TOKEN";
const SIDECAR_NAME: &str = "devcolleague-backend";

#[derive(Clone, Serialize)]
struct BackendInfo {
    port: u16,
    token: String,
}

struct BackendState {
    info: BackendInfo,
    child: Mutex<Option<Child>>,
}

#[tauri::command]
fn backend_info(state: State<'_, BackendState>) -> BackendInfo {
    state.info.clone()
}

fn pick_free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    Ok(listener.local_addr()?.port())
}

fn generate_token() -> String {
    let mut bytes = [0u8; 32];
    getrandom::getrandom(&mut bytes).expect("無法產生隨機 session token");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// 建立後端啟動指令。開發模式直接使用 backend/.venv 的 Python，避免多一層 uv 行程。
fn backend_command() -> Result<Command, String> {
    if cfg!(debug_assertions) {
        let backend_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../backend");
        let python = if cfg!(windows) {
            backend_dir.join(".venv/Scripts/python.exe")
        } else {
            backend_dir.join(".venv/bin/python")
        };
        if !python.exists() {
            return Err(format!(
                "找不到 {}，請先在 backend/ 執行 `uv sync`",
                python.display()
            ));
        }
        let mut cmd = Command::new(python);
        cmd.args(["-m", "devcolleague"]).current_dir(backend_dir);
        Ok(cmd)
    } else {
        let exe_dir = std::env::current_exe()
            .map_err(|e| e.to_string())?
            .parent()
            .ok_or("無法取得執行檔目錄")?
            .to_path_buf();
        let name = if cfg!(windows) {
            format!("{SIDECAR_NAME}.exe")
        } else {
            SIDECAR_NAME.to_string()
        };
        Ok(Command::new(exe_dir.join(name)))
    }
}

fn spawn_backend(info: &BackendInfo) -> Result<Child, String> {
    let mut cmd = backend_command()?;
    cmd.arg("--port")
        .arg(info.port.to_string())
        .arg("--exit-on-stdin-eof")
        .env(SESSION_TOKEN_ENV, &info.token)
        .stdin(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    cmd.spawn().map_err(|e| format!("無法啟動後端：{e}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let port = pick_free_port()?;
            let info = BackendInfo {
                port,
                token: generate_token(),
            };
            let child = spawn_backend(&info).map_err(|e| -> Box<dyn std::error::Error> { e.into() })?;
            app.manage(BackendState {
                info,
                child: Mutex::new(Some(child)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_info])
        .build(tauri::generate_context!())
        .expect("啟動 DevColleague 失敗");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = handle.try_state::<BackendState>() {
                if let Some(mut child) = state.child.lock().ok().and_then(|mut c| c.take()) {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        }
    });
}
