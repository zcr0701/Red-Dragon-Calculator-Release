// 红龙计算器 C++ 原型 —— 桌面窗口版（WebView2 宿主）
// 特性：无边框暗色窗口、自绘标题栏、置顶、半透明、一键截图(可选 OCR 回填)、
//       WebView2 内嵌页面（不再开浏览器标签页）。
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <windowsx.h>
#include <dwmapi.h>
#include <gdiplus.h>
#include <shellapi.h>

#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <string>
#include <vector>

#include "WebView2.h"
#include "server.h"

#ifndef DWMWA_WINDOW_CORNER_PREFERENCE
#define DWMWA_WINDOW_CORNER_PREFERENCE 33
#endif
#ifndef DWMWCP_ROUND
#define DWMWCP_ROUND 2
#endif

namespace {

// ---------- 窗口布局 ----------
constexpr int kTitleBarH = 44;
constexpr int kBtnW = 46;
constexpr int kBtnH = 30;

enum TitleButton {
    kBtnOcr = 0,
    kBtnPin,
    kBtnOpacity,
    kBtnMin,
    kBtnClose,
    kBtnCount
};

struct ButtonRect {
    RECT rc;
    const wchar_t* label;
};

ButtonRect g_btns[kBtnCount] = {
    {{0, 0, 0, 0}, L"OCR"},
    {{0, 0, 0, 0}, L"置顶"},
    {{0, 0, 0, 0}, L"透明"},
    {{0, 0, 0, 0}, L"—"},
    {{0, 0, 0, 0}, L"✕"},
};

int g_hover = -1;
bool g_pinned = false;
bool g_translucent = false;
BYTE g_opacity = 255;
int g_winW = 1120;
int g_winH = 820;

HWND g_hwnd = nullptr;
HFONT g_fontTitle = nullptr;
HFONT g_fontSub = nullptr;
HFONT g_fontBtn = nullptr;

// ---------- WebView2 ----------
ICoreWebView2Environment* g_env = nullptr;
ICoreWebView2Controller* g_controller = nullptr;
ICoreWebView2* g_webview = nullptr;
EventRegistrationToken g_msgToken = {};
std::wstring g_url;
std::wstring g_exeDir;

constexpr UINT WM_APP_OCR = WM_APP + 1;
constexpr UINT kTimerId = 1;

// ---------- 颜色 ----------
const COLORREF kBg = RGB(0x1e, 0x1e, 0x1e);
const COLORREF kPanel = RGB(0x25, 0x25, 0x26);
const COLORREF kBorder = RGB(0x3c, 0x3c, 0x3c);
const COLORREF kText = RGB(0xd4, 0xd4, 0xd4);
const COLORREF kMuted = RGB(0x9d, 0x9d, 0x9d);
const COLORREF kAccent = RGB(0x0e, 0x63, 0x9c);
const COLORREF kHover = RGB(0x3a, 0x3d, 0x41);

// ---------- 小工具 ----------
std::wstring utf8_to_wide(const std::string& s) {
    if (s.empty()) {
        return L"";
    }
    int len = MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), nullptr,
                                  0);
    std::wstring out(len, L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.data(), (int)s.size(), out.data(), len);
    return out;
}

std::string wide_to_utf8(const std::wstring& s) {
    if (s.empty()) {
        return "";
    }
    int len = WideCharToMultiByte(CP_UTF8, 0, s.data(), (int)s.size(), nullptr,
                                  0, nullptr, nullptr);
    std::string out(len, '\0');
    WideCharToMultiByte(CP_UTF8, 0, s.data(), (int)s.size(), out.data(), len,
                        nullptr, nullptr);
    return out;
}

void FillRectColor(HDC dc, const RECT& rc, COLORREF color) {
    HBRUSH br = CreateSolidBrush(color);
    FillRect(dc, &rc, br);
    DeleteObject(br);
}

std::wstring GetExeDir() {
    wchar_t buf[MAX_PATH] = {0};
    DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring p(buf, n);
    size_t pos = p.find_last_of(L"\\/");
    return pos == std::wstring::npos ? L"." : p.substr(0, pos);
}

std::wstring GetEnvW(const wchar_t* name) {
    DWORD n = GetEnvironmentVariableW(name, nullptr, 0);
    if (n == 0) {
        return L"";
    }
    std::wstring out(n, L'\0');
    GetEnvironmentVariableW(name, out.data(), n);
    return out;
}

std::wstring ReadRegString(HKEY root, const wchar_t* sub,
                           const wchar_t* name) {
    HKEY hk = nullptr;
    if (RegOpenKeyExW(root, sub, 0, KEY_READ | KEY_WOW64_64KEY, &hk) !=
        ERROR_SUCCESS) {
        return L"";
    }
    wchar_t buf[128] = {0};
    DWORD size = sizeof(buf);
    DWORD type = 0;
    std::wstring out;
    if (RegQueryValueExW(hk, name, nullptr, &type, (LPBYTE)buf, &size) ==
            ERROR_SUCCESS &&
        type == REG_SZ) {
        out = buf;
    }
    RegCloseKey(hk);
    return out;
}

bool VersionGreater(const std::wstring& a, const std::wstring& b) {
    auto parts = [](const std::wstring& v) {
        std::vector<int> r;
        std::wstring cur;
        for (wchar_t ch : v) {
            if (ch == L'.') {
                r.push_back(_wtoi(cur.c_str()));
                cur.clear();
            } else if (ch >= L'0' && ch <= L'9') {
                cur += ch;
            }
        }
        r.push_back(_wtoi(cur.c_str()));
        return r;
    };
    std::vector<int> pa = parts(a);
    std::vector<int> pb = parts(b);
    size_t n = std::max(pa.size(), pb.size());
    for (size_t i = 0; i < n; ++i) {
        int x = i < pa.size() ? pa[i] : 0;
        int y = i < pb.size() ? pb[i] : 0;
        if (x != y) {
            return x > y;
        }
    }
    return false;
}

// ---------- 布局 / 命中 ----------
void LayoutButtons() {
    int x = g_winW - 8 - kBtnW;
    int y = (kTitleBarH - kBtnH) / 2;
    for (int i = kBtnCount - 1; i >= 0; --i) {
        g_btns[i].rc = {x, y, x + kBtnW, y + kBtnH};
        x -= kBtnW + 6;
    }
}

int HitButton(const POINT& pt) {
    for (int i = 0; i < kBtnCount; ++i) {
        if (PtInRect(&g_btns[i].rc, pt)) {
            return i;
        }
    }
    return -1;
}

// ---------- 绘制 ----------
void Paint(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC dc = BeginPaint(hwnd, &ps);

    RECT full = {0, 0, g_winW, g_winH};
    FillRectColor(dc, full, kBg);

    RECT bar = {0, 0, g_winW, kTitleBarH};
    FillRectColor(dc, bar, kPanel);
    RECT line = {0, kTitleBarH - 1, g_winW, kTitleBarH};
    FillRectColor(dc, line, kBorder);

    RECT title = {14, 0, g_winW - 400, kTitleBarH};
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, kText);
    HFONT old = (HFONT)SelectObject(dc, g_fontTitle);
    DrawTextW(dc, L"红龙计算器", -1, &title,
              DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    SelectObject(dc, g_fontSub);
    SetTextColor(dc, kMuted);
    title.left += 92;
    DrawTextW(dc, L"束搜索 · 桌面版", -1, &title,
              DT_LEFT | DT_VCENTER | DT_SINGLELINE);
    SelectObject(dc, g_fontBtn);

    for (int i = 0; i < kBtnCount; ++i) {
        RECT rc = g_btns[i].rc;
        bool active = (i == kBtnPin && g_pinned) ||
                      (i == kBtnOpacity && g_translucent);
        if (i == g_hover) {
            FillRectColor(dc, rc, active ? RGB(0x1b, 0x4d, 0x73) : kHover);
        } else if (active) {
            FillRectColor(dc, rc, RGB(0x0d, 0x3d, 0x5f));
        }
        RECT bd = rc;
        InflateRect(&bd, -1, -1);
        HPEN pen = CreatePen(PS_SOLID, 1,
                             (i == g_hover || active) ? kAccent : kBorder);
        HPEN oldPen = (HPEN)SelectObject(dc, pen);
        HBRUSH oldBr = (HBRUSH)SelectObject(dc, GetStockObject(NULL_BRUSH));
        Rectangle(dc, bd.left, bd.top, bd.right, bd.bottom);
        SelectObject(dc, oldBr);
        SelectObject(dc, oldPen);
        DeleteObject(pen);

        SetTextColor(dc, active ? RGB(0x9c, 0xdc, 0xfe) : kText);
        RECT tr = rc;
        DrawTextW(dc, g_btns[i].label, -1, &tr,
                  DT_CENTER | DT_VCENTER | DT_SINGLELINE);
    }

    SelectObject(dc, old);
    EndPaint(hwnd, &ps);
}

// ---------- 页面消息 ----------
std::string JsEscape(const std::string& s) {
    std::string out;
    for (unsigned char ch : s) {
        switch (ch) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default: out += (char)ch;
        }
    }
    return out;
}

void ToastPage(const std::wstring& msg) {
    if (!g_webview) {
        return;
    }
    std::string m = JsEscape(wide_to_utf8(msg));
    std::string js =
        "(()=>{const s=document.getElementById('status');if(s)s.innerHTML="
        "'<span class=\"ok\">" +
        m + "</span>'})()";
    g_webview->ExecuteScript(utf8_to_wide(js).c_str(), nullptr);
}

void InjectHandAndRun(const std::string& handText) {
    if (!g_webview) {
        return;
    }
    std::string js = "(()=>{const el=document.getElementById('hand');if(!el)"
                     "return;el.value=\"" +
                     JsEscape(handText) +
                     "\";run();})()";
    g_webview->ExecuteScript(utf8_to_wide(js).c_str(), nullptr);
}

// ---------- 截图 + OCR ----------
BOOL CALLBACK FindGameWindow(HWND hwnd, LPARAM lp) {
    if (!IsWindowVisible(hwnd) || IsIconic(hwnd)) {
        return TRUE;
    }
    wchar_t title[256] = {0};
    GetWindowTextW(hwnd, title, 256);
    if (wcsstr(title, L"炉石传说") || wcsstr(title, L"Hearthstone") ||
        wcsstr(title, L"hearthstone")) {
        *reinterpret_cast<HWND*>(lp) = hwnd;
        return FALSE;
    }
    return TRUE;
}

struct TitleMatch {
    std::wstring key;
    HWND found = nullptr;
};

BOOL CALLBACK FindByTitleProc(HWND hwnd, LPARAM lp) {
    auto* tm = reinterpret_cast<TitleMatch*>(lp);
    if (!IsWindowVisible(hwnd) || IsIconic(hwnd)) {
        return TRUE;
    }
    wchar_t title[256] = {0};
    GetWindowTextW(hwnd, title, 256);
    if (tm->key.empty()) {
        return TRUE;
    }
    if (wcsstr(title, tm->key.c_str())) {
        tm->found = hwnd;
        return FALSE;
    }
    return TRUE;
}

bool FindCaptureRect(RECT& out) {
    TitleMatch tm;
    tm.key = GetEnvW(L"RD_CAPTURE_WINDOW");
    if (!tm.key.empty()) {
        EnumWindows(FindByTitleProc, (LPARAM)&tm);
    }
    if (!tm.found) {
        EnumWindows(FindGameWindow, (LPARAM)&tm.found);
    }
    if (tm.found) {
        GetWindowRect(tm.found, &out);
        return true;
    }
    out = {GetSystemMetrics(SM_XVIRTUALSCREEN),
           GetSystemMetrics(SM_YVIRTUALSCREEN),
           GetSystemMetrics(SM_XVIRTUALSCREEN) +
               GetSystemMetrics(SM_CXVIRTUALSCREEN),
           GetSystemMetrics(SM_YVIRTUALSCREEN) +
               GetSystemMetrics(SM_CYVIRTUALSCREEN)};
    return true;
}

int GetPngEncoderClsid(CLSID* pClsid) {
    UINT num = 0, size = 0;
    Gdiplus::GetImageEncodersSize(&num, &size);
    if (size == 0) {
        return -1;
    }
    auto* info = (Gdiplus::ImageCodecInfo*)malloc(size);
    Gdiplus::GetImageEncoders(num, size, info);
    int found = -1;
    for (UINT i = 0; i < num; ++i) {
        if (wcscmp(info[i].MimeType, L"image/png") == 0) {
            *pClsid = info[i].Clsid;
            found = (int)i;
            break;
        }
    }
    free(info);
    return found;
}

bool CaptureRectToFile(const RECT& rc, const std::wstring& path) {
    int w = rc.right - rc.left;
    int h = rc.bottom - rc.top;
    if (w <= 0 || h <= 0) {
        return false;
    }
    HDC screen = GetDC(nullptr);
    HDC mem = CreateCompatibleDC(screen);
    HBITMAP bmp = CreateCompatibleBitmap(screen, w, h);
    if (!bmp) {
        DeleteDC(mem);
        ReleaseDC(nullptr, screen);
        return false;
    }
    HGDIOBJ old = SelectObject(mem, bmp);
    BitBlt(mem, 0, 0, w, h, screen, rc.left, rc.top, SRCCOPY | CAPTUREBLT);
    SelectObject(mem, old);

    CLSID clsid;
    bool ok = false;
    if (GetPngEncoderClsid(&clsid) >= 0) {
        Gdiplus::Bitmap gbmp(bmp, nullptr);
        ok = gbmp.Save(path.c_str(), &clsid, nullptr) == Gdiplus::Ok;
    }
    DeleteObject(bmp);
    DeleteDC(mem);
    ReleaseDC(nullptr, screen);
    return ok;
}

std::string RunCommandCaptureOutput(const std::wstring& cmdline) {
    SECURITY_ATTRIBUTES sa = {sizeof(sa), nullptr, TRUE};
    HANDLE readPipe = nullptr, writePipe = nullptr;
    if (!CreatePipe(&readPipe, &writePipe, &sa, 0)) {
        return "";
    }
    SetHandleInformation(readPipe, HANDLE_FLAG_INHERIT, 0);

    STARTUPINFOW si = {sizeof(si)};
    si.dwFlags = STARTF_USESTDHANDLES;
    si.hStdOutput = writePipe;
    si.hStdError = writePipe;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    PROCESS_INFORMATION pi = {};

    std::wstring cmd = cmdline;
    BOOL ok = CreateProcessW(nullptr, cmd.data(), nullptr, nullptr, TRUE,
                             CREATE_NO_WINDOW, nullptr, nullptr, &si, &pi);
    CloseHandle(writePipe);
    std::string out;
    if (ok) {
        char buf[4096];
        DWORD n = 0;
        while (ReadFile(readPipe, buf, sizeof(buf), &n, nullptr) && n > 0) {
            out.append(buf, n);
        }
        WaitForSingleObject(pi.hProcess, 30000);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
    CloseHandle(readPipe);
    return out;
}

std::vector<std::string> ExtractRecTexts(const std::string& json) {
    std::vector<std::string> out;
    size_t pos = json.find("\"rec_texts\"");
    if (pos == std::string::npos) {
        return out;
    }
    size_t arr = json.find('[', pos);
    if (arr == std::string::npos) {
        return out;
    }
    size_t i = arr + 1;
    while (i < json.size()) {
        i = json.find('"', i);
        if (i == std::string::npos) {
            break;
        }
        size_t j = i + 1;
        std::string s;
        while (j < json.size()) {
            if (json[j] == '\\') {
                if (j + 1 < json.size()) {
                    s += json[j + 1];
                }
                j += 2;
                continue;
            }
            if (json[j] == '"') {
                break;
            }
            s += json[j];
            ++j;
        }
        if (!s.empty()) {
            out.push_back(s);
        }
        i = j + 1;
        if (i < json.size() && json[i] == ']') {
            break;
        }
    }
    return out;
}

std::wstring GetOcrCommand() {
    std::wstring env = GetEnvW(L"RD_OCR_CMD");
    if (!env.empty()) {
        return env;
    }
    std::wstring ppocr = g_exeDir + L"\\ppocr.exe";
    if (GetFileAttributesW(ppocr.c_str()) != INVALID_FILE_ATTRIBUTES) {
        std::wstring models = g_exeDir + L"\\..\\models";
        return L"\"" + ppocr + L"\" ocr --input \"%IMG%\" --device cpu "
               L"--use_doc_orientation_classify False "
               L"--use_doc_unwarping False --use_textline_orientation False "
               L"--text_detection_model_dir \"" + models +
               L"\\PP-OCRv5_server_det_infer\" "
               L"--text_recognition_model_dir \"" + models +
               L"\\PP-OCRv5_server_rec_infer\"";
    }
    return L"";
}

void RunOcrCapture() {
    RECT rc;
    if (!FindCaptureRect(rc)) {
        ToastPage(L"截图失败：未找到可捕获的窗口");
        return;
    }
    std::wstring path = g_exeDir + L"\\capture.png";
    if (!CaptureRectToFile(rc, path)) {
        ToastPage(L"截图失败");
        return;
    }

    std::wstring cmd = GetOcrCommand();
    if (cmd.empty()) {
        ToastPage(L"已截图：" + path + L"（未配置 OCR，见 README）");
        return;
    }
    std::wstring full = cmd;
    size_t pos = full.find(L"%IMG%");
    if (pos != std::wstring::npos) {
        full.replace(pos, 5, L"\"" + path + L"\"");
    } else {
        full += L" \"" + path + L"\"";
    }

    std::string out = RunCommandCaptureOutput(full);
    std::vector<std::string> texts = ExtractRecTexts(out);
    if (texts.empty() && !out.empty()) {
        // 兜底：把整段输出按行作为卡名
        std::string cur;
        for (char ch : out) {
            if (ch == '\n' || ch == '\r') {
                if (!cur.empty()) {
                    texts.push_back(cur);
                    cur.clear();
                }
            } else {
                cur += ch;
            }
        }
        if (!cur.empty()) {
            texts.push_back(cur);
        }
    }
    std::string hand;
    for (size_t i = 0; i < texts.size(); ++i) {
        if (i) {
            hand += ",";
        }
        hand += texts[i];
    }
    if (hand.empty()) {
        ToastPage(L"OCR 未识别到文字（输出见控制台日志）");
        return;
    }
    InjectHandAndRun(hand);
    ToastPage(L"OCR 完成，已填入手牌并开始计算");
}

// ---------- WebView2 COM 回调 ----------
template <typename IFace>
class ComHandler : public IFace {
public:
    ComHandler() = default;

    STDMETHODIMP QueryInterface(REFIID riid, void** ppv) override {
        if (riid == IID_IUnknown) {
            *ppv = static_cast<IUnknown*>(this);
            AddRef();
            return S_OK;
        }
        if (riid == __uuidof(IFace)) {
            *ppv = static_cast<IFace*>(this);
            AddRef();
            return S_OK;
        }
        *ppv = nullptr;
        return E_NOINTERFACE;
    }

    STDMETHODIMP_(ULONG) AddRef() override {
        return InterlockedIncrement(&refs_);
    }

    STDMETHODIMP_(ULONG) Release() override {
        ULONG r = InterlockedDecrement(&refs_);
        if (r == 0) {
            delete this;
        }
        return r;
    }

private:
    LONG refs_ = 1;
};

class WebMessageHandler
    : public ComHandler<ICoreWebView2WebMessageReceivedEventHandler> {
public:
    STDMETHODIMP Invoke(ICoreWebView2* sender,
                        ICoreWebView2WebMessageReceivedEventArgs* args) override {
        LPWSTR msg = nullptr;
        BOOL ok = FALSE;
        if (SUCCEEDED(args->get_TryGetWebMessageAsString(&msg, &ok)) && ok &&
            msg) {
            std::wstring m = msg;
            CoTaskMemFree(msg);
            if (m == L"ocr" || m == L"capture") {
                PostMessage(g_hwnd, WM_APP_OCR, 0, 0);
            }
        }
        return S_OK;
    }
};

class ControllerCreatedHandler
    : public ComHandler<ICoreWebView2CreateCoreWebView2ControllerCompletedHandler> {
public:
    STDMETHODIMP Invoke(HRESULT result,
                        ICoreWebView2Controller* controller) override {
        if (FAILED(result) || !controller) {
            MessageBoxW(g_hwnd, L"创建 WebView2 控制器失败。", L"红龙计算器",
                        MB_ICONERROR);
            PostQuitMessage(1);
            return S_OK;
        }
        controller->AddRef();
        g_controller = controller;
        RECT rc = {0, kTitleBarH, g_winW, g_winH};
        controller->put_Bounds(rc);

        ICoreWebView2* wv = nullptr;
        if (FAILED(controller->get_CoreWebView2(&wv)) || !wv) {
            MessageBoxW(g_hwnd, L"获取 WebView2 核心对象失败。", L"红龙计算器",
                        MB_ICONERROR);
            PostQuitMessage(1);
            return S_OK;
        }
        g_webview = wv;  // get_CoreWebView2 已 AddRef

        ICoreWebView2Settings* st = nullptr;
        if (SUCCEEDED(wv->get_Settings(&st)) && st) {
            st->put_AreDevToolsEnabled(FALSE);
            st->put_AreDefaultContextMenusEnabled(FALSE);
            st->Release();
        }

        auto* mh = new WebMessageHandler();
        wv->add_WebMessageReceived(mh, &g_msgToken);
        mh->Release();

        wv->Navigate(g_url.c_str());
        return S_OK;
    }
};

class EnvCreatedHandler
    : public ComHandler<ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler> {
public:
    STDMETHODIMP Invoke(HRESULT result,
                        ICoreWebView2Environment* env) override {
        if (FAILED(result) || !env) {
            MessageBoxW(g_hwnd,
                        L"WebView2 环境创建失败。请安装 WebView2 Runtime。",
                        L"红龙计算器", MB_ICONERROR);
            PostQuitMessage(1);
            return S_OK;
        }
        env->AddRef();
        g_env = env;

        auto* handler = new ControllerCreatedHandler();
        HRESULT hr = env->CreateCoreWebView2Controller(g_hwnd, handler);
        handler->Release();
        if (FAILED(hr)) {
            MessageBoxW(g_hwnd, L"创建 WebView2 控制器失败。", L"红龙计算器",
                        MB_ICONERROR);
            PostQuitMessage(1);
        }
        return S_OK;
    }
};

// ---------- WebView2 初始化 ----------
bool InitWebView2Dll() {
    if (LoadLibraryW(L"WebView2Loader.dll")) {
        return true;
    }
    const wchar_t* kSub =
        L"SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\"
        L"{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
    std::wstring ver = ReadRegString(HKEY_LOCAL_MACHINE, kSub, L"pv");
    std::vector<std::wstring> roots = {
        L"C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application",
        L"C:\\Program Files\\Microsoft\\EdgeWebView\\Application",
        GetEnvW(L"LOCALAPPDATA") +
            L"\\Microsoft\\EdgeWebView\\Application"};
    if (!ver.empty()) {
        std::wstring p = roots[0] + L"\\" + ver +
                         L"\\EmbeddedBrowserWebView.dll";
        if (GetFileAttributesW(p.c_str()) != INVALID_FILE_ATTRIBUTES) {
            if (LoadLibraryW(p.c_str())) {
                return true;
            }
        }
    }
    for (const auto& root : roots) {
        std::wstring best;
        WIN32_FIND_DATAW fd = {};
        HANDLE hf = FindFirstFileW((root + L"\\*").c_str(), &fd);
        if (hf == INVALID_HANDLE_VALUE) {
            continue;
        }
        do {
            if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) {
                continue;
            }
            if (VersionGreater(fd.cFileName, best)) {
                best = fd.cFileName;
            }
        } while (FindNextFileW(hf, &fd));
        FindClose(hf);
        if (!best.empty()) {
            std::wstring p =
                root + L"\\" + best + L"\\EmbeddedBrowserWebView.dll";
            if (GetFileAttributesW(p.c_str()) != INVALID_FILE_ATTRIBUTES &&
                LoadLibraryW(p.c_str())) {
                return true;
            }
        }
    }
    return false;
}

void InitWebView2Async() {
    if (!InitWebView2Dll()) {
        MessageBoxW(g_hwnd,
                    L"未找到 WebView2 Runtime。\n请安装 Microsoft Edge WebView2。",
                    L"红龙计算器", MB_ICONERROR);
        PostQuitMessage(1);
        return;
    }
    auto fn = (PFN_CREATE_CORE_WEBVIEW2_ENVIRONMENT_WITH_OPTIONS)
        GetProcAddress(GetModuleHandleW(L"WebView2Loader.dll"),
                       "CreateCoreWebView2EnvironmentWithOptions");
    if (!fn) {
        // 直接加载运行时 DLL 时，函数也在其中
        HMODULE rt = GetModuleHandleW(L"EmbeddedBrowserWebView.dll");
        fn = (PFN_CREATE_CORE_WEBVIEW2_ENVIRONMENT_WITH_OPTIONS)
            GetProcAddress(rt ? rt : GetModuleHandleW(L"WebView2Loader.dll"),
                           "CreateCoreWebView2EnvironmentWithOptions");
    }
    if (!fn) {
        MessageBoxW(g_hwnd, L"WebView2 Loader 初始化失败。", L"红龙计算器",
                    MB_ICONERROR);
        PostQuitMessage(1);
        return;
    }

    std::wstring userData = g_exeDir + L"\\webview2_userdata";
    auto* handler = new EnvCreatedHandler();
    HRESULT hr = fn(nullptr, userData.c_str(), nullptr, handler);
    handler->Release();
    if (FAILED(hr)) {
        MessageBoxW(g_hwnd, L"WebView2 初始化失败。", L"红龙计算器",
                    MB_ICONERROR);
        PostQuitMessage(1);
    }
}

// ---------- 窗口过程 ----------
void OnButton(int b) {
    switch (b) {
        case kBtnClose:
            PostMessage(g_hwnd, WM_CLOSE, 0, 0);
            break;
        case kBtnMin:
            ShowWindow(g_hwnd, SW_MINIMIZE);
            break;
        case kBtnPin:
            g_pinned = !g_pinned;
            SetWindowPos(g_hwnd, g_pinned ? HWND_TOPMOST : HWND_NOTOPMOST, 0,
                         0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
            InvalidateRect(g_hwnd, nullptr, FALSE);
            break;
        case kBtnOpacity:
            g_translucent = !g_translucent;
            g_opacity = g_translucent ? 220 : 255;
            SetLayeredWindowAttributes(g_hwnd, 0, g_opacity, LWA_ALPHA);
            InvalidateRect(g_hwnd, nullptr, FALSE);
            break;
        case kBtnOcr:
            RunOcrCapture();
            break;
        default:
            break;
    }
}

LRESULT CALLBACK WndProc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp) {
    switch (msg) {
        case WM_CREATE: {
            DWORD pref = DWMWCP_ROUND;
            DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, &pref,
                                  sizeof(pref));
            SetTimer(hwnd, kTimerId, 300, nullptr);
            return 0;
        }
        case WM_TIMER:
            if (rdcweb::is_stopped()) {
                PostMessage(hwnd, WM_CLOSE, 0, 0);
            }
            return 0;
        case WM_SIZE: {
            g_winW = LOWORD(lp);
            g_winH = HIWORD(lp);
            LayoutButtons();
            if (g_controller) {
                RECT rc = {0, kTitleBarH, g_winW, g_winH};
                g_controller->put_Bounds(rc);
            }
            InvalidateRect(hwnd, nullptr, FALSE);
            return 0;
        }
        case WM_GETMINMAXINFO: {
            auto* mmi = reinterpret_cast<MINMAXINFO*>(lp);
            mmi->ptMinTrackSize.x = 880;
            mmi->ptMinTrackSize.y = 560;
            return 0;
        }
        case WM_PAINT:
            Paint(hwnd);
            return 0;
        case WM_NCHITTEST: {
            POINT pt = {GET_X_LPARAM(lp), GET_Y_LPARAM(lp)};
            ScreenToClient(hwnd, &pt);
            if (pt.y < kTitleBarH) {
                if (HitButton(pt) >= 0) {
                    return HTCLIENT;
                }
                return HTCAPTION;
            }
            break;
        }
        case WM_MOUSEMOVE: {
            POINT pt = {GET_X_LPARAM(lp), GET_Y_LPARAM(lp)};
            int h = HitButton(pt);
            if (h != g_hover) {
                g_hover = h;
                TRACKMOUSEEVENT tme = {sizeof(tme), TME_LEAVE, hwnd, 0};
                TrackMouseEvent(&tme);
                InvalidateRect(hwnd, nullptr, FALSE);
            }
            return 0;
        }
        case WM_MOUSELEAVE:
            if (g_hover != -1) {
                g_hover = -1;
                InvalidateRect(hwnd, nullptr, FALSE);
            }
            return 0;
        case WM_LBUTTONUP: {
            POINT pt = {GET_X_LPARAM(lp), GET_Y_LPARAM(lp)};
            int b = HitButton(pt);
            if (b >= 0) {
                OnButton(b);
                return 0;
            }
            break;
        }
        case WM_APP_OCR:
            RunOcrCapture();
            return 0;
        case WM_CLOSE:
            DestroyWindow(hwnd);
            return 0;
        case WM_DESTROY:
            KillTimer(hwnd, kTimerId);
            rdcweb::stop_server();
            if (g_webview) {
                g_webview->Release();
                g_webview = nullptr;
            }
            if (g_controller) {
                g_controller->Release();
                g_controller = nullptr;
            }
            if (g_env) {
                g_env->Release();
                g_env = nullptr;
            }
            PostQuitMessage(0);
            return 0;
        default:
            break;
    }
    return DefWindowProc(hwnd, msg, wp, lp);
}

}  // namespace

int WINAPI WinMain(HINSTANCE hInst, HINSTANCE, LPSTR, int) {
    SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    Gdiplus::GdiplusStartupInput gsi;
    ULONG_PTR gdiToken = 0;
    Gdiplus::GdiplusStartup(&gdiToken, &gsi, nullptr);

    int port = 0;
    if (!rdcweb::start_server(port)) {
        MessageBoxW(nullptr, L"启动本地服务失败（端口 17890-17899 均被占用）。",
                    L"红龙计算器", MB_ICONERROR);
        return 1;
    }
    g_url = L"http://127.0.0.1:" + std::to_wstring(port);
    g_exeDir = GetExeDir();

    WNDCLASSEXW wc = {sizeof(wc)};
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = WndProc;
    wc.hInstance = hInst;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = nullptr;
    wc.lpszClassName = L"RedDragonCalcDesktop";
    RegisterClassExW(&wc);

    g_fontTitle = CreateFontW(-16, 0, 0, 0, FW_SEMIBOLD, FALSE, FALSE, FALSE,
                              DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                              CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                              DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontSub = CreateFontW(-13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                            CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                            DEFAULT_PITCH, L"Microsoft YaHei UI");
    g_fontBtn = CreateFontW(-13, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                            DEFAULT_CHARSET, OUT_DEFAULT_PRECIS,
                            CLIP_DEFAULT_PRECIS, CLEARTYPE_QUALITY,
                            DEFAULT_PITCH, L"Microsoft YaHei UI");

    RECT wr = {0, 0, g_winW, g_winH};
    AdjustWindowRectEx(&wr, WS_POPUP, FALSE, WS_EX_LAYERED);
    int x = (GetSystemMetrics(SM_CXSCREEN) - g_winW) / 2;
    int y = (GetSystemMetrics(SM_CYSCREEN) - g_winH) / 2;
    g_hwnd = CreateWindowExW(
        WS_EX_LAYERED, wc.lpszClassName, L"红龙计算器 · 桌面版",
        WS_POPUP | WS_VISIBLE, x, y, g_winW, g_winH, nullptr, nullptr, hInst,
        nullptr);
    if (!g_hwnd) {
        rdcweb::stop_server();
        return 1;
    }
    LayoutButtons();
    ShowWindow(g_hwnd, SW_SHOW);
    UpdateWindow(g_hwnd);

    InitWebView2Async();

    MSG msg = {};
    while (GetMessageW(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    if (g_fontTitle) {
        DeleteObject(g_fontTitle);
    }
    if (g_fontSub) {
        DeleteObject(g_fontSub);
    }
    if (g_fontBtn) {
        DeleteObject(g_fontBtn);
    }
    Gdiplus::GdiplusShutdown(gdiToken);
    CoUninitialize();
    return 0;
}
