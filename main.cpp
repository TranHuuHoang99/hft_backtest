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

class Solution {
public:
    int getNumberOfBacklogOrders(vector<vector<int>>& orders) {
        map<int,int> orders_map[2]; // 0 buy, 1 sell
        set<int> orders_set[2];
        for (auto vec : orders) {
            int price = vec[0];
            int amount = vec[1];
            int type = vec[2];
            if (type == 0) {
                while (amount > 0 && !orders_set[1-type].empty()) {
                    auto it = orders_set[1-type].begin();
                    if (price >= *it) {
                        int diff = min(orders_map[1-type][*it], amount);
                        orders_map[1-type][*it] -= diff;
                        amount -= diff;
                        if (orders_map[1-type][*it] == 0) {
                            orders_set[1-type].erase(it);
                        }
                    } else {
                        break;
                    }
                }
                if (amount > 0) {
                    orders_set[type].insert(price);
                    orders_map[type][price] += amount;
                }
            } else {
                while (amount > 0 && !orders_set[1-type].empty()) {
                    auto it = prev(orders_set[1-type].end());
                    if (price <= *it) {
                        int diff = min(orders_map[1-type][*it], amount);
                        orders_map[1-type][*it] -= diff;
                        amount -= diff;
                        if (orders_map[1-type][*it] == 0) {
                            orders_set[1-type].erase(it);
                        }
                    } else {
                        break;
                    }
                }
                if (amount > 0) {
                    orders_set[type].insert(price);
                    orders_map[type][price] += amount;
                }
            }
        }
        long long ret = 0ll;
        long long mod = 1e9+7;
        for (int i = 0; i <= 1; i++) {
            for (pair<int,int> p : orders_map[i]) {
                ret = (ret % mod + p.second % mod) % mod;
            }
        }
        return ret;
    }
};

void solve(void) {

}
signed main(void) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}





