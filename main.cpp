/*
**************************************************************************
    author     : hoangprodn
    email  	   : thhoang08091999@gmail.com
    local time : 2026-11-06 22:33:48
**************************************************************************
*/
#include <bits/stdc++.h>
#define int long long
#define pi acos(-1.0)
using namespace std;

struct node_value {
    int price;
    int seq;
    node_value(void) {
        price = INT32_MAX;
        seq = 0;
    }
};
vector<node_value> arr(10);
struct node_index {
    int idx;
    node_index(const int _idx) {
        idx = _idx;
    }
    bool operator < (const node_index& other) const {
        if (arr[idx].price == arr[other.idx].price) {
            return arr[idx].seq > arr[other.idx].seq;
        }
        // take the order with higher price
        return arr[idx].price < arr[other.idx].price;
    }
};
priority_queue<node_index> pq;
void solve(void) {
    for (int i = 0; i < 10; i++) {
        node_value temp;
        temp.price = i;
        temp.seq = 10;
        arr[i] = temp;
        pq.emplace(node_index(i));
    }
    while (!pq.empty()) {
        node_index cur_node_idx = pq.top();
        pq.pop();
        const int& idx = cur_node_idx.idx;
        const node_value& cur_val = arr[idx];
        cout << cur_val.price << ' ' << cur_val.seq << '\n';
    }
}
signed main(void) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}





