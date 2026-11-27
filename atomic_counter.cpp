/*
**************************************************************************
    author     : hoangprodn
    email  	   : thhoang08091999@gmail.com
    local time : 2026-11-26 15:09:21
**************************************************************************
*/
#include <bits/stdc++.h>
#define int long long
using namespace std;

class AtomicCounter {
public:
    AtomicCounter(int initialValue) {
        m_cnt.store(initialValue);
    }

    int incrementAndGet() {
        int cur_val = m_cnt.load(memory_order_acquire);
        while (!m_cnt.compare_exchange_weak(cur_val, cur_val + 1, memory_order_release, memory_order_relaxed));
        return cur_val + 1;
    }

    int addAndGet(int delta) {
        int cur_val = m_cnt.load(memory_order_acquire);
        while (!m_cnt.compare_exchange_weak(cur_val, cur_val + delta, memory_order_release, memory_order_relaxed));
        return cur_val + delta;
    }

    int get() {
        return m_cnt.load();
    }
private:
    atomic<int> m_cnt;
};

void solve(void) {

}
signed main(void) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}






