#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include "WebView2.h"

// Verify __uuidof works with MinGW for the WebView2 interfaces we need.
static const IID* g_a = &__uuidof(ICoreWebView2Controller);
static const IID* g_b = &__uuidof(ICoreWebView2Environment);
static const IID* g_c = &__uuidof(ICoreWebView2CreateCoreWebView2EnvironmentCompletedHandler);
static const IID* g_d = &__uuidof(ICoreWebView2CreateCoreWebView2ControllerCompletedHandler);
static const IID* g_e = &__uuidof(ICoreWebView2WebMessageReceivedEventHandler);

int main() {
    return (g_a && g_b && g_c && g_d && g_e) ? 0 : 1;
}
