// 红龙计算器 C++ 原型 —— VS Code 风格 Web GUI
// C++ 内置本地 HTTP 服务，把暗色 Web 界面嵌入可执行文件，浏览器自动打开。
// 接口：
//   GET  /              返回前端页面（内嵌 HTML/CSS/JS）
//   POST /api/search    执行束搜索，返回结构化 JSON
//   POST /api/cancel    中止正在进行的搜索
//   POST /api/shutdown  退出服务
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "cards.h"
#include "engine.h"
#include "format.h"
#include "server.h"
#include "search.h"

namespace {

std::atomic<bool> g_stop{false};    // 服务停止标志
std::atomic<bool> g_cancel{false};  // 搜索取消标志
SOCKET g_listen = INVALID_SOCKET;
std::mutex g_search_mutex;

// ---------- JSON 小工具 ----------
std::string json_escape(const std::string& s) {
    std::string out;
    for (unsigned char ch : s) {
        switch (ch) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (ch < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", ch);
                    out += buf;
                } else {
                    out += (char)ch;
                }
        }
    }
    return out;
}

std::string json_get_string(const std::string& body, const std::string& key) {
    std::string pat = "\"" + key + "\"";
    size_t pos = body.find(pat);
    if (pos == std::string::npos) {
        return "";
    }
    pos = body.find(':', pos + pat.size());
    if (pos == std::string::npos) {
        return "";
    }
    pos = body.find('"', pos + 1);
    if (pos == std::string::npos) {
        return "";
    }
    std::string out;
    bool esc = false;
    for (size_t i = pos + 1; i < body.size(); ++i) {
        char c = body[i];
        if (esc) {
            switch (c) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                default: out += c;
            }
            esc = false;
        } else if (c == '\\') {
            esc = true;
        } else if (c == '"') {
            break;
        } else {
            out += c;
        }
    }
    return out;
}

int json_get_int(const std::string& body, const std::string& key, int fallback) {
    std::string pat = "\"" + key + "\"";
    size_t pos = body.find(pat);
    if (pos == std::string::npos) {
        return fallback;
    }
    pos = body.find(':', pos + pat.size());
    if (pos == std::string::npos) {
        return fallback;
    }
    size_t start = body.find_first_of("-0123456789", pos + 1);
    if (start == std::string::npos) {
        return fallback;
    }
    size_t end = start + 1;
    while (end < body.size() &&
           (std::isdigit((unsigned char)body[end]) || body[end] == '.')) {
        ++end;
    }
    try {
        return std::stoi(body.substr(start, end - start));
    } catch (...) {
        return fallback;
    }
}

std::vector<std::string> parse_names(const std::string& text) {
    std::vector<std::string> names;
    std::string current;
    for (char ch : text) {
        if (ch == ',' || ch == '\n') {
            size_t start = current.find_first_not_of(" \t");
            size_t end = current.find_last_not_of(" \t");
            if (start != std::string::npos) {
                names.push_back(current.substr(start, end - start + 1));
            }
            current.clear();
        } else {
            current += ch;
        }
    }
    size_t start = current.find_first_not_of(" \t");
    size_t end = current.find_last_not_of(" \t");
    if (start != std::string::npos) {
        names.push_back(current.substr(start, end - start + 1));
    }
    return names;
}

// ---------- 搜索结果 -> 紧凑 JSON ----------
std::string results_json(const std::vector<rdc::GameState>& states,
                         double elapsed_ms, bool stopped) {
    std::vector<rdc::GameState> unique;
    std::set<std::string> seen;
    for (const auto& st : states) {
        std::string key;
        for (size_t i = 0; i < st.path.size(); ++i) {
            if (i) key += "\x1f";
            key += st.path[i];
        }
        if (seen.count(key)) {
            continue;
        }
        seen.insert(key);
        unique.push_back(st);
    }
    rdc::sort_path_states(unique);

    int alex_count = 0, non_alex = 0, best_alex = 0, best_damage = 0;
    for (const auto& st : unique) {
        if (st.alex_play_count > 0) {
            ++alex_count;
            best_alex = std::max(best_alex, st.alex_play_count);
            best_damage = std::max(best_damage, st.alex_damage);
        } else {
            ++non_alex;
        }
    }

    std::ostringstream out;
    out << "{\n";
    out << "  \"ok\": true,\n";
    out << "  \"stopped\": " << (stopped ? "true" : "false") << ",\n";
    out << "  \"elapsed_ms\": " << (long long)elapsed_ms << ",\n";
    out << "  \"unique_paths\": " << (int)unique.size() << ",\n";
    out << "  \"alex_paths\": " << alex_count << ",\n";
    out << "  \"non_alex_paths\": " << non_alex << ",\n";
    out << "  \"best_alex\": " << best_alex << ",\n";
    out << "  \"best_damage\": " << best_damage << ",\n";
    out << "  \"results\": [\n";
    for (size_t i = 0; i < unique.size(); ++i) {
        const auto& st = unique[i];
        out << "    {\"path\": \"" << json_escape(rdc::format_path_text(st.path))
            << "\", \"alex_play_count\": " << st.alex_play_count
            << ", \"alex_damage\": " << st.alex_damage
            << ", \"mana\": " << st.mana
            << ", \"initial_mana_crystals\": " << st.initial_mana_crystals
            << ", \"initial_mana\": " << st.initial_mana << "}";
        if (i + 1 < unique.size()) out << ",";
        out << "\n";
    }
    out << "  ]\n";
    out << "}";
    return out.str();
}

// ---------- 内嵌前端页面 ----------
const char* kIndexHtml = R"RDCHTML(<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>红龙计算器 · C++ 束搜索</title>
<style>
:root{--bg:#1e1e1e;--panel:#252526;--panel2:#2d2d30;--border:#3c3c3c;--text:#d4d4d4;--muted:#9d9d9d;--accent:#0e639c;--accent2:#1177bb;--green:#89d185;--red:#f48771;--yellow:#e5c07b;--mono:'Cascadia Mono','Consolas','Courier New',monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:'Segoe UI','Microsoft YaHei',sans-serif;font-size:13px}
header{display:flex;align-items:center;gap:12px;padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
header h1{font-size:15px;margin:0;font-weight:600}
.dot{width:9px;height:9px;border-radius:50%;background:var(--green);display:inline-block}
.muted{color:var(--muted)}
#status{margin-left:auto;display:flex;align-items:center;gap:7px}
.wrap{max-width:1200px;margin:0 auto;padding:16px;display:grid;grid-template-columns:350px 1fr;gap:16px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:14px}
label{display:block;margin:10px 0 4px;color:var(--muted)}
textarea,input{width:100%;background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:6px 8px;font:inherit;outline:none}
textarea{resize:vertical;font-family:var(--mono);line-height:1.5}
textarea:focus,input:focus{border-color:var(--accent2)}
.row{display:flex;gap:10px}
.row>div{flex:1}
.btns{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}
button{background:var(--panel2);color:var(--text);border:1px solid var(--border);border-radius:4px;padding:7px 14px;cursor:pointer;font:inherit}
button:hover{background:#3a3d41}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
button.primary:hover{background:var(--accent2)}
button:disabled{opacity:.45;cursor:default}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.stat{background:var(--panel2);border:1px solid var(--border);border-radius:6px;padding:8px 14px;min-width:108px}
.stat b{display:block;font-size:20px;color:#fff;line-height:1.2}
.stat span{color:var(--muted);font-size:12px}
.path{font-family:var(--mono);font-size:12px;padding:8px 10px;border:1px solid var(--border);border-radius:5px;margin-bottom:8px;background:#1a1a1a;line-height:1.7;word-break:break-all}
.badges{float:right;display:flex;gap:6px;margin-left:8px}
.badge{padding:1px 8px;border-radius:10px;font-size:11px;background:#094771;color:#9cdcfe;white-space:nowrap}
.badge.dmg{background:#5a2d0c;color:#e5c07b}
.badge.mana{background:#1f3b2c;color:#89d185}
.hint{color:var(--muted);font-size:12px;line-height:1.7}
.spinner{display:inline-block;width:12px;height:12px;border:2px solid #555;border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px}
@keyframes spin{to{transform:rotate(360deg)}}
.err{color:var(--red)}
.ok{color:var(--green)}
</style>
</head>
<body>
<header>
  <h1>红龙计算器</h1><span class="muted">C++ 束搜索 · 本地服务</span>
  <span id="status"><span class="dot"></span> 就绪</span>
  <button id="quit">退出服务</button>
</header>
<div class="wrap">
  <div class="card">
    <label for="hand">手牌（卡名逗号/换行分隔，支持简称）</label>
    <textarea id="hand" rows="4">鲨鱼之灵,狐人老千,斯卡布斯·刀油,幸运币,幸运币,幸运币,生命的缚誓者阿莱克丝塔萨</textarea>
    <label for="deck">牌库（可选）</label>
    <textarea id="deck" rows="2"></textarea>
    <label for="play">先打出（可选）</label>
    <textarea id="play" rows="1"></textarea>
    <datalist id="cards"></datalist>
    <div class="row">
      <div><label for="crystals">水晶</label><input id="crystals" value="10"></div>
      <div><label for="mana">法力</label><input id="mana" value="10"></div>
    </div>
    <div class="row">
      <div><label for="width">束宽</label><input id="width" value="600"></div>
      <div><label for="depth">最大深度</label><input id="depth" value="18"></div>
    </div>
    <div class="row">
      <div><label for="maxpaths">路径上限</label><input id="maxpaths" value="1000000"></div>
      <div><label for="minalex">龙数下限</label><input id="minalex" value="1"></div>
    </div>
    <div class="row">
      <div><label for="maxalex">龙数上限</label><input id="maxalex" value="10"></div>
      <div><label for="showlimit">显示条数</label><input id="showlimit" value="50"></div>
    </div>
    <div class="btns">
      <button id="run" class="primary">开始计算</button>
      <button id="cancel" disabled>中止</button>
      <button id="example">填入示例</button>
      <button id="clear">清空结果</button>
      <button id="export">导出 JSON</button>
    </div>
  </div>
  <div class="card">
    <div class="stats" id="stats"></div>
    <div id="results"><div class="hint">填写手牌后点击“开始计算”。卡名支持简称自动匹配（如 刀油 → 斯卡布斯·刀油）。<br>示例：鲨鱼之灵 + 3×幸运币 + 红龙 → 1 龙 16 伤。</div></div>
  </div>
</div>
<script>
const CARDS=["暗影步","伪造的幸运币","幸运币","伺机待发","殒命暗影","黑水弯刀","邪恶短刀","垂钓时光","挖掘宝藏","闪避","狐人老千","赤烟·腾武","行骗","锯齿骨刺","疾速矿锄","异教地图","潜伏帷幕","晦鳞巢母","鲨鱼之灵","斯卡布斯·刀油","乐队经理精英牛头人酋长","幻觉药水","舞动全场（ft.迦罗娜）","生命的缚誓者阿莱克丝塔萨","可疑交易","暗影施法者"];
const $=id=>document.getElementById(id);
const dl=$("cards");CARDS.forEach(n=>{const o=document.createElement("option");o.value=n;dl.appendChild(o)});
let lastJson=null;
function setBusy(b){$("run").disabled=b;$("cancel").disabled=!b;$("status").innerHTML=b?'<span class="spinner"></span> 计算中…':'<span class="dot"></span> 就绪'}
function setStatus(html,cls){const s=$("status");s.innerHTML=html;s.className=cls||""}
function num(id,fb){const v=parseInt($(id).value,10);return isNaN(v)?fb:v}
async function run(){
  const body={hand:$( "hand").value,deck:$("deck").value,play:$("play").value,
    crystals:num("crystals",10),mana:num("mana",10),beam_width:num("width",3000),
    max_depth:num("depth",100),max_paths:num("maxpaths",1000000),
    min_alex:num("minalex",1),max_alex:num("maxalex",10),show_limit:num("showlimit",200)};
  setBusy(true);
  try{
    const r=await fetch("/api/search",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();lastJson=j;render(j);
    setStatus('<span class="dot ok"></span> 完成');
  }catch(e){setStatus('<span class="err">请求失败：'+e.message+'</span>')}
  setBusy(false);
}
function render(j){
  const st=$("stats");st.innerHTML="";
  const mk=(v,label)=>'<div class="stat"><b>'+v+'</b><span>'+label+'</span></div>';
  st.innerHTML=mk(j.unique_paths,"唯一终局路径")+mk(j.alex_paths,"红龙路径")+mk(j.best_alex+" 龙","最优龙数")+mk(j.best_damage+" 伤","最高伤害")+mk((j.elapsed_ms/1000).toFixed(1)+"s","耗时");
  const box=$("results");
  if(j.stopped){box.innerHTML='<div class="hint err">已中止，以下为当前最优结果。</div>';}
  else{box.innerHTML="";}
  let n=0;
  for(const it of j.results){
    if(n>=num("showlimit",200))break;n++;
    const d=document.createElement("div");d.className="path";
    const badges='<span class="badges"><span class="badge">'+it.alex_play_count+' 龙</span><span class="badge dmg">'+it.alex_damage+' 伤</span><span class="badge mana">剩 '+it.mana+' 法</span></span>';
    d.innerHTML=badges+n+". "+it.path;box.appendChild(d);
  }
  if(n===0&&!j.stopped)box.innerHTML+='<div class="hint">未找到可打出红龙的路径。</div>';
}
async function cancel(){try{await fetch("/api/cancel",{method:"POST"})}catch(e){}}
async function quit(){try{await fetch("/api/shutdown",{method:"POST"})}catch(e){}window.close()}
function fillExample(){
  $("hand").value="鲨鱼之灵,狐人老千,斯卡布斯·刀油,幸运币,幸运币,幸运币,生命的缚誓者阿莱克丝塔萨";
  $("deck").value="";$("play").value="";$("crystals").value="10";$("mana").value="10";
  $("width").value="600";$("depth").value="18";$("maxpaths").value="1000000";
  $("minalex").value="1";$("maxalex").value="10";$("showlimit").value="50";
}
function clearAll(){lastJson=null;$("stats").innerHTML="";$("results").innerHTML='<div class="hint">结果已清空。</div>'}
function exportJson(){
  if(!lastJson){alert("当前没有可导出的结果");return}
  const blob=new Blob([JSON.stringify(lastJson,null,2)],{type:"application/json"});
  const a=document.createElement("a");a.href=URL.createObjectURL(blob);
  a.download="red_dragon_result.json";a.click();URL.revokeObjectURL(a.href);
}
$("run").onclick=run;$("cancel").onclick=cancel;$("quit").onclick=quit;
$("example").onclick=fillExample;$("clear").onclick=clearAll;$("export").onclick=exportJson;
$("hand").addEventListener("keydown",e=>{if(e.key==="Enter"&&(e.ctrlKey||e.metaKey))run()});
</script>
</body>
</html>
)RDCHTML";

// ---------- HTTP ----------
void send_all(SOCKET s, const char* data, int len) {
    int sent = 0;
    while (sent < len) {
        int n = send(s, data + sent, len - sent, 0);
        if (n <= 0) {
            return;
        }
        sent += n;
    }
}

void send_response(SOCKET s, const std::string& status,
                   const std::string& content_type, const std::string& body) {
    std::string head = "HTTP/1.1 " + status + "\r\n";
    head += "Content-Type: " + content_type + "\r\n";
    head += "Content-Length: " + std::to_string(body.size()) + "\r\n";
    head += "Connection: close\r\n\r\n";
    send_all(s, head.data(), (int)head.size());
    send_all(s, body.data(), (int)body.size());
}

std::string read_request(SOCKET s, std::string& method, std::string& path,
                         std::string& body) {
    std::string buf;
    char tmp[4096];
    std::string header_end = "\r\n\r\n";
    while (buf.find(header_end) == std::string::npos) {
        int n = recv(s, tmp, sizeof(tmp), 0);
        if (n <= 0) {
            return "recv error";
        }
        buf.append(tmp, n);
        if (buf.size() > 1 << 20) {
            return "request too large";
        }
    }
    size_t end = buf.find(header_end);
    std::string head = buf.substr(0, end);
    body = buf.substr(end + 4);

    size_t sp1 = head.find(' ');
    size_t sp2 = head.find(' ', sp1 + 1);
    if (sp1 == std::string::npos || sp2 == std::string::npos) {
        return "bad request line";
    }
    method = head.substr(0, sp1);
    path = head.substr(sp1 + 1, sp2 - sp1 - 1);

    // Content-Length
    std::string lower = head;
    for (auto& c : lower) {
        c = (char)tolower((unsigned char)c);
    }
    size_t clp = lower.find("content-length:");
    if (clp != std::string::npos) {
        size_t val_start = clp + 15;
        int len = 0;
        while (val_start < lower.size() &&
               (lower[val_start] == ' ' || lower[val_start] == '\t')) {
            ++val_start;
        }
        while (val_start < lower.size() &&
               std::isdigit((unsigned char)lower[val_start])) {
            len = len * 10 + (lower[val_start] - '0');
            ++val_start;
        }
        while ((int)body.size() < len) {
            int n = recv(s, tmp, sizeof(tmp), 0);
            if (n <= 0) {
                break;
            }
            body.append(tmp, n);
        }
    }
    return "";
}

void handle_search(SOCKET client, const std::string& body) {
    std::lock_guard<std::mutex> lock(g_search_mutex);
    g_cancel = false;

    std::string hand = json_get_string(body, "hand");
    std::string deck = json_get_string(body, "deck");
    std::string play = json_get_string(body, "play");
    int crystals = json_get_int(body, "crystals", 10);
    int mana = json_get_int(body, "mana", crystals);
    int beam_width = json_get_int(body, "beam_width", 3000);
    int max_depth = json_get_int(body, "max_depth", 100);
    int max_paths = json_get_int(body, "max_paths", 1000000);
    int min_alex = json_get_int(body, "min_alex", 1);
    int max_alex = json_get_int(body, "max_alex", 10);

    std::string result;
    try {
        rdc::GameState state = rdc::create_state(
            parse_names(deck), !deck.empty(), parse_names(hand), crystals, mana);
        for (const auto& name : parse_names(play)) {
            rdc::play_card_by_name(state, name);
        }
        auto t0 = std::chrono::steady_clock::now();
        std::vector<rdc::GameState> states = rdc::beam_search_paths(
            state, max_depth, max_paths, max_alex, min_alex, beam_width, 0.0,
            &g_cancel);
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now() - t0)
                      .count();
        result = results_json(states, (double)ms, g_cancel.load());
    } catch (const std::exception& e) {
        std::ostringstream out;
        out << "{\"ok\": false, \"error\": \"" << json_escape(e.what())
            << "\"}";
        result = out.str();
    }
    send_response(client, "200 OK", "application/json; charset=utf-8", result);
}

void handle_client(SOCKET client) {
    std::string method, path, body;
    std::string err = read_request(client, method, path, body);
    if (!err.empty()) {
        send_response(client, "400 Bad Request", "text/plain; charset=utf-8",
                      err);
        closesocket(client);
        return;
    }
    if (method == "GET" && (path == "/" || path == "/index.html")) {
        send_response(client, "200 OK", "text/html; charset=utf-8",
                      std::string(kIndexHtml));
    } else if (method == "GET" && path == "/favicon.ico") {
        send_response(client, "204 No Content", "text/plain", "");
    } else if (method == "POST" && path == "/api/search") {
        handle_search(client, body);
    } else if (method == "POST" && path == "/api/cancel") {
        g_cancel = true;
        send_response(client, "200 OK", "application/json; charset=utf-8",
                      "{\"ok\": true}");
    } else if (method == "POST" && path == "/api/shutdown") {
        send_response(client, "200 OK", "application/json; charset=utf-8",
                      "{\"ok\": true, \"message\": \"服务已退出\"}");
        g_stop = true;
        if (g_listen != INVALID_SOCKET) {
            closesocket(g_listen);
            g_listen = INVALID_SOCKET;
        }
    } else {
        send_response(client, "404 Not Found", "text/plain; charset=utf-8",
                      "not found");
    }
    closesocket(client);
}

}  // namespace

namespace {

void server_loop(SOCKET sock) {
    while (!g_stop.load()) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(sock, &rfds);
        timeval tv = {0, 200000};  // 200ms 轮询，保证 stop_server 能及时生效
        int ready = select(0, &rfds, nullptr, nullptr, &tv);
        if (ready == SOCKET_ERROR) {
            if (g_stop.load()) {
                break;
            }
            continue;
        }
        if (ready == 0) {
            continue;
        }
        SOCKET client = accept(sock, nullptr, nullptr);
        if (client == INVALID_SOCKET) {
            continue;
        }
        std::thread(handle_client, client).detach();
    }
}

}  // namespace

bool start_server(int& port_out) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        return false;
    }

    SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock == INVALID_SOCKET) {
        WSACleanup();
        return false;
    }
    BOOL reuse = TRUE;
    setsockopt(sock, SOL_SOCKET, SO_REUSEADDR, (const char*)&reuse,
               sizeof(reuse));

    sockaddr_in addr = {};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

    int port = 0;
    for (int p = 17890; p <= 17899; ++p) {
        addr.sin_port = htons((u_short)p);
        if (bind(sock, (sockaddr*)&addr, sizeof(addr)) == 0) {
            port = p;
            break;
        }
    }
    if (port == 0) {
        closesocket(sock);
        WSACleanup();
        return false;
    }
    listen(sock, 8);
    g_listen = sock;
    port_out = port;

    std::thread(server_loop, sock).detach();
    return true;
}

void stop_server() {
    g_stop = true;
    if (g_listen != INVALID_SOCKET) {
        closesocket(g_listen);
        g_listen = INVALID_SOCKET;
    }
}

bool is_stopped() {
    return g_stop.load();
}

void request_cancel() {
    g_cancel = true;
}
