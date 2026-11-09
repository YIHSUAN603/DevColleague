// Release 版不顯示額外的主控台視窗
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    devcolleague_lib::run()
}
