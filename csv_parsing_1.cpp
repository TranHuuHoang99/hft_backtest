/*
**************************************************************************
    author     : hoangprodn
    email  	   : thhoang08091999@gmail.com
    local time : 2026-11-07 10:23:29
**************************************************************************
*/
#include <bits/stdc++.h>
#define int long long
#define pi acos(-1.0)
using namespace std;

struct asset_info {
    string asset_name;
    uint64_t asset_value;
    string exchange;
    asset_info() {
        asset_name = "";
        asset_value = 0;
        exchange = "";
    }
    void clear(void) {
        asset_name = "";
        asset_value = 0;
        exchange = "";
    }
};

bool check_valid_number(string_view sv, uint64_t& ret_val) {
    if (sv.empty()) return false;
    auto [ptr, ec] = std::from_chars(sv.data(), sv.data()+sv.size(), ret_val);
    // true if error code is valid and pointer have to go to the last character of the string
    return (ec == std::errc() && ptr == sv.data() + sv.size());
}

void trim(string& str) {
    if (str.empty()) return;
    int left = 0;
    int right = str.size()-1;
    while (left <= right && (str[left] == ' ' || str[left] == ',' || str[left] == ';'))
        left++;
    while (right >= left && (str[right] == ' ' || str[right] == ',' || str[right] == ';'))
        right--;
    if (left > right) {
        str = "";
        return;
    }
    str = str.substr(left, right - left + 1);
}

string serialize(string_view sv) {
    if (sv.empty()) return "\"\"";
    bool is_need_quote = false;
    string ret = "";
    ret.reserve(sv.size()+50);
    for (char c : sv) {
        if (c == '"') {
            is_need_quote = true;
            ret.push_back('\"');
            ret.push_back('\"');
        } else {
            ret.push_back(c);
            if (c == ',' || c == ';' || c == ' ' || c == '\'') {
                is_need_quote = true;
            }
        }
    }
    if (is_need_quote) return "\"" + ret + "\"";
    return string(sv);
}

string str;
asset_info streaming_info;
void solve(void) {
    while (getline(cin, str)) {
        streaming_info.clear();
        string cur_token = "";
        // create a buffer to avoid reallocate in parsing runtime
        cur_token.reserve(str.length());
        int state = 0;
        bool in_single_quote = false;
        bool in_double_quote = false;
        for (int i = 0; i < str.size(); i++) {
            if (in_single_quote && str[i] == '\'' && i+1 < str.size() && str[i+1] == '\'') {
                cur_token.push_back('\'');
                i++;
                continue;
            }
            if (in_double_quote && str[i] == '"' && i+1 < str.size() && str[i+1] == '"') {
                cur_token.push_back('"');
                i++;
                continue;
            }
            if (str[i] == '\'' && !in_double_quote) {
                in_single_quote = !in_single_quote;
                continue;
            }
            if (str[i] == '"' && !in_single_quote) {
                in_double_quote = !in_double_quote;
                continue;
            }
            if (in_single_quote || in_double_quote || (str[i] != ';' && str[i] != ',')) {
                cur_token.push_back(str[i]);
                continue;
            }
            // cut current token and parse
            if (state == 0) { // first field
                trim(cur_token);
                streaming_info.asset_name = cur_token;
                cur_token = "";
                state = 1;
            } else if (state == 1) { // second field require uint64_t
                uint64_t ret_val = 0;
                trim(cur_token);
                if (check_valid_number(cur_token, ret_val)) {
                    streaming_info.asset_value = ret_val;
                    cur_token = "";
                } else {
                    streaming_info.exchange = cur_token;
                }
                state = 2;
            } else {
                cur_token.push_back(str[i]);
            }
        }
        trim(cur_token);
        if (state == 0) {
            streaming_info.asset_name = cur_token;
        } else if (state == 1) {
            uint64_t ret_val = 0;
            if (check_valid_number(cur_token, ret_val)) {
                streaming_info.asset_value = ret_val;
            } else {
                streaming_info.exchange = cur_token;
            }
        } else {
            streaming_info.exchange += cur_token;
        }
        // serialize
        string output = serialize(streaming_info.asset_name) + "," + to_string(streaming_info.asset_value) + "," + serialize(streaming_info.exchange);
        cout << output << '\n';
    }
}
signed main(void) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}





