#pragma once

namespace rdcweb {

// 在 127.0.0.1 上启动本地 HTTP 服务（端口 17890-17899 自动递增）。
// 成功返回 true 并通过 port_out 传出实际端口；失败返回 false。
bool start_server(int& port_out);

// 请求关闭服务：置停止标志并关闭监听 socket，accept 循环尽快退出。
void stop_server();

// 是否已被请求关闭（页面右上角“退出服务”或外部调用 stop_server）。
bool is_stopped();

// 请求取消当前搜索（/api/cancel）。
void request_cancel();

}  // namespace rdcweb
