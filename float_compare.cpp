/*
**************************************************************************
    author     : hoangprodn
    email  	   : thhoang08091999@gmail.com
    local time : 2026-11-27 15:10:15
**************************************************************************
*/
#include <bits/stdc++.h>
#define int long long
using namespace std;

bool f_compare(const double& a, const double& b, const double abs_eps = 1e-9, const double rel_eps = 1e-12) {
    if (!isfinite(a) || !isfinite(b)) return false;
    if (a == b) return true;
    const double diff = fabs(a - b);
    if (!isfinite(diff)) return false;
    const double largest = max(fabs(a), fabs(b));
    return diff <= max(abs_eps, rel_eps * largest);
}
void solve(void) {
    const double a = numeric_limits<double>::lowest();
    const double b = numeric_limits<double>::max();
    cout << a << ' ' << b << '\n';
    cout << f_compare(a, b) << '\n';
}
signed main(void) {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);
    solve();
    return 0;
}






