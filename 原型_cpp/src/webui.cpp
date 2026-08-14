// 红龙计算器 C++ 原型 —— Web 浏览器模式入口
// 启动本地 HTTP 服务并自动打开默认浏览器。
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstdio>
#include <string>

#include "server.h"

namespace {

std::wstring u8_to_wide(const std::string& s) {
    if (s.empty()) {
        return L"";
    }
    int len =
        MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr, 0);
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), out.data(), len);
    return out;
}

}  // namespace

int main() {
    SetConsoleOutputCP(CP_UTF8);

    int port = 0;
    if (!rdcweb::start_server(port)) {
        std::printf("启动服务失败（端口 17890-17899 均被占用）\n");
        return 1;
    }

    char url[128];
    std::snprintf(url, sizeof(url), "http://127.0.0.1:%d", port);
    std::printf("红龙计算器服务已启动：%s\n", url);
    std::printf("按 Ctrl+C 或点击页面右上角“退出服务”关闭。\n");
    ShellExecuteW(nullptr, L"open", u8_to_wide(url).c_str(), nullptr, nullptr,
                  SW_SHOWNORMAL);

    while (!rdcweb::is_stopped()) {
        Sleep(200);
    }
    std::printf("服务已退出。\n");
    return 0;
}
