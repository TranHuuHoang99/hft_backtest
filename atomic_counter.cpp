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
        return 0;
    }

    int addAndGet(int delta) {
        return 0;
    }

    int get() {
        return 0;
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






