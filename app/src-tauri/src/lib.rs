//! DevColleague 桌面外殼：啟動並管理 Python 後端子行程。
//!
//! 啟動流程：
//! 1. 取得一個可用的動態 port、產生隨機 session token
//! 2. 啟動後端（開發模式使用 backend/.venv 的 Python；發佈版使用 sidecar 執行檔）
//!    - port 以命令列參數傳入；token 以環境變數傳入，不出現在命令列
//!    - 後端的 stdin 接到本行程，本行程結束（包含異常結束）時後端會自動退出
//! 3. 前端透過 `backend_info` 指令取得 port 與 token
//! 4. App 結束時結束後端子行程
//!
//! 背景常駐（F16）：
//! - 關閉視窗只是隱藏，App 常駐系統匣；從系統匣選「結束」才完全停止
//! - 開機啟動時帶 `--autostart` 參數，不顯示主視窗
//! - 只允許一個實例；再次開啟時改為顯示既有的主視窗
//! - 前端收到後端的通知事件後呼叫 `show_notification`，點擊通知時顯示主視窗並通知前端開啟對應頁面

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

const SESSION_TOKEN_ENV: &str = "DEVCOLLEAGUE_SESSION_TOKEN";
const SIDECAR_NAME: &str = "devcolleague-backend";
const MAIN_WINDOW: &str = "main";
/// 開機啟動時附加的參數，用來判斷是否要隱藏主視窗
const AUTOSTART_ARG: &str = "--autostart";
/// 點擊通知後送給前端的事件名稱
const NOTIFICATION_ACTIVATED_EVENT: &str = "notification-activated";
/// 記錄「已套用過開機啟動預設值」的檔案，之後以使用者的設定為準
const AUTOSTART_MARKER: &str = "autostart-initialized";

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

fn show_main_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_WINDOW) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn setup_tray(app: &tauri::App) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "開啟 DevColleague", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "結束", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;

    let mut builder = TrayIconBuilder::with_id("main")
        .tooltip("DevColleague")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main_window(app),
            "quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show_main_window(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// 第一次執行發佈版時預設開啟開機啟動；之後以使用者在設定頁的選擇為準。
/// 開發模式不自動開啟，避免把除錯版執行檔登錄為開機啟動。
fn apply_autostart_default(app: &tauri::App) {
    use tauri_plugin_autostart::ManagerExt;

    if cfg!(debug_assertions) {
        return;
    }
    let Ok(dir) = app.path().app_config_dir() else {
        return;
    };
    let marker = dir.join(AUTOSTART_MARKER);
    if marker.exists() {
        return;
    }
    if app.autolaunch().enable().is_ok() {
        let _ = std::fs::create_dir_all(&dir);
        let _ = std::fs::write(marker, "");
    }
}

/// 開啟對應位置的資訊，原樣轉交前端（例如 `{"page": "chat", "conversation_id": "..."}`）
type NotificationTarget = Option<serde_json::Value>;

#[cfg(windows)]
fn notification_app_id(app: &AppHandle) -> String {
    use tauri_winrt_notification::Toast;

    // 系統通知需要已登錄的 AppUserModelID；安裝版的捷徑以 identifier 登錄，
    // 未安裝（直接從 target 目錄執行）時借用 PowerShell 的 ID，否則通知不會顯示
    let in_target_dir = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|p| p.to_path_buf()))
        .map(|dir| dir.ends_with("target/debug") || dir.ends_with("target/release"))
        .unwrap_or(true);
    if in_target_dir {
        Toast::POWERSHELL_APP_ID.to_string()
    } else {
        app.config().identifier.clone()
    }
}

#[cfg(windows)]
#[tauri::command]
fn show_notification(app: AppHandle, title: String, body: String, target: NotificationTarget) -> Result<(), String> {
    use tauri::Emitter;
    use tauri_winrt_notification::Toast;

    let handle = app.clone();
    Toast::new(&notification_app_id(&app))
        .title(&title)
        .text1(&body)
        .on_activated(move |_action| {
            show_main_window(&handle);
            let _ = handle.emit(NOTIFICATION_ACTIVATED_EVENT, target.clone());
            Ok(())
        })
        .show()
        .map_err(|e| format!("無法顯示系統通知：{e}"))
}

#[cfg(not(windows))]
#[tauri::command]
fn show_notification(_app: AppHandle, _title: String, _body: String, _target: NotificationTarget) -> Result<(), String> {
    // 目前只發佈 Windows 版
    Err("此作業系統尚未支援系統通知".into())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        // single-instance 需最先註冊：再次開啟 App 時改為顯示既有視窗
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main_window(app);
        }))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![AUTOSTART_ARG]),
        ))
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

            setup_tray(app)?;
            apply_autostart_default(app);

            // 主視窗預設隱藏（見 tauri.conf.json）；開機啟動時維持隱藏，只常駐系統匣
            if !std::env::args().any(|a| a == AUTOSTART_ARG) {
                show_main_window(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            // 關閉視窗時改為隱藏，App 與後端繼續在背景執行
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == MAIN_WINDOW {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![backend_info, show_notification])
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
