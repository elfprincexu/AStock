import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// --- JWT interceptor: attach token to every request ---
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('astock_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// --- 401 interceptor: auto-logout on expired token ---
let _onUnauthorized = null // callback set by App.vue
api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response && err.response.status === 401 && _onUnauthorized) {
      _onUnauthorized()
    }
    return Promise.reject(err)
  },
)

export default {
  // Auth hook — App.vue sets this so interceptor can trigger logout
  setOnUnauthorized(fn) { _onUnauthorized = fn },

  // ── Authentication ─────────────────────────────────────────────
  login(username, password) {
    const form = new URLSearchParams()
    form.append('username', username)
    form.append('password', password)
    return api.post('/auth/login', form, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    })
  },
  getMe() {
    return api.get('/auth/me')
  },
  changePassword(oldPassword, newPassword) {
    return api.put('/auth/me/password', { old_password: oldPassword, new_password: newPassword })
  },
  // Admin: user management
  getUsers() {
    return api.get('/auth/users')
  },
  createUser(data) {
    return api.post('/auth/users', data)
  },
  updateUser(id, data) {
    return api.put(`/auth/users/${id}`, data)
  },
  deleteUser(id) {
    return api.delete(`/auth/users/${id}`)
  },
  getPermissionKeys() {
    return api.get('/auth/permissions')
  },

  // ── Stocks ─────────────────────────────────────────────────────
  getStocks(activeOnly = false, { keyword = '', limit = 0 } = {}) {
    return api.get('/stocks/', { params: { active_only: activeOnly, keyword, limit } })
  },
  getStocksEnriched({ favorites_only = false, keyword = '', page = 1, page_size = 50 } = {}) {
    return api.get('/stocks/enriched', { params: { favorites_only, keyword, page, page_size } })
  },
  getFavorites() {
    return api.get('/stocks/', { params: { favorites_only: true } })
  },
  getFavoritesEnriched() {
    return api.get('/stocks/enriched', { params: { favorites_only: true, page_size: 500 } })
  },
  addStock(data) {
    return api.post('/stocks/', data)
  },
  updateStock(id, data) {
    return api.put(`/stocks/${id}`, data)
  },
  deleteStock(id) {
    return api.delete(`/stocks/${id}`)
  },
  searchStocks(keyword) {
    return api.get('/stocks/search', { params: { keyword } })
  },
  fetchStock(id) {
    return api.post(`/stocks/${id}/fetch`)
  },
  fetchStockLite(id) {
    return api.post(`/stocks/${id}/fetch-lite`)
  },
  fetchAllStocks() {
    return api.post('/stocks/fetch-all', null, { timeout: 300000 })
  },
  // Quotes
  getSnapshots(stockId, limit = 100) {
    return api.get(`/quotes/snapshots/${stockId}`, { params: { limit } })
  },
  getKlines(stockId, limit = 2000) {
    return api.get(`/quotes/klines/${stockId}`, { params: { limit } })
  },
  getLogs(limit = 100) {
    return api.get('/quotes/logs', { params: { limit } })
  },
  deleteErrorLogs() {
    return api.delete('/quotes/logs/errors')
  },
  deleteAllLogs() {
    return api.delete('/quotes/logs/all')
  },
  getProfile(stockId) {
    return api.get(`/quotes/profile/${stockId}`)
  },
  getRealtimeQuote(stockId) {
    return api.get(`/quotes/realtime/${stockId}`, { timeout: 15000 })
  },
  getRealtimeQuoteByCode(code) {
    return api.get(`/quotes/realtime-by-code/${code}`, { timeout: 15000 })
  },
  getIntradayKlines(stockId, { scale = 5, limit = 240 } = {}) {
    return api.get(`/quotes/intraday/${stockId}`, { params: { scale, limit } })
  },
  getIntradayKlinesByCode(code, { scale = 5, limit = 240 } = {}) {
    return api.get(`/quotes/intraday-by-code/${code}`, { params: { scale, limit } })
  },
  // Screener
  getPresets() {
    return api.get('/screener/presets')
  },
  getIndustries() {
    return api.get('/screener/industries')
  },
  runScreen(criteria) {
    return api.post('/screener/screen', criteria)
  },
  runPreset(key, limit = 50) {
    return api.post(`/screener/preset/${key}`, null, { params: { limit } })
  },
  // Schedule
  getScheduleSettings() {
    return api.get('/schedule/settings')
  },
  updateScheduleSettings(data) {
    return api.put('/schedule/settings', data)
  },
  getScheduleStatus() {
    return api.get('/schedule/status')
  },
  triggerDailyUpdate() {
    return api.post('/schedule/trigger')
  },
  // AI Analysis (DSA integration)
  aiHealth() {
    return api.get('/ai/health')
  },
  aiAnalyze(stockCode, stockName, { reportType = 'detailed', forceRefresh = false } = {}) {
    return api.post('/ai/analyze', {
      stock_code: stockCode,
      stock_name: stockName,
      report_type: reportType,
      force_refresh: forceRefresh,
    })
  },
  aiStatus(taskId) {
    return api.get(`/ai/status/${taskId}`)
  },
  aiHistory(stockCode, limit = 5) {
    return api.get(`/ai/history/${stockCode}`, { params: { limit } })
  },
  aiReport(recordId) {
    return api.get(`/ai/report/${recordId}`)
  },
  // Configuration
  getConfigSettings() {
    return api.get('/config/settings')
  },
  updateConfigSettings(data) {
    return api.put('/config/settings', data)
  },
  testLLM() {
    return api.post('/config/test-llm', null, { timeout: 60000 })
  },
  // Strategy Trading
  getStrategies(params = {}) {
    return api.get('/trade/strategies', { params })
  },
  getStrategy(id) {
    return api.get(`/trade/strategies/${id}`)
  },
  createStrategy(data) {
    return api.post('/trade/strategies', data)
  },
  updateStrategy(id, data) {
    return api.put(`/trade/strategies/${id}`, data)
  },
  deleteStrategy(id) {
    return api.delete(`/trade/strategies/${id}`)
  },
  activateStrategy(id) {
    return api.post(`/trade/strategies/${id}/activate`)
  },
  pauseStrategy(id) {
    return api.post(`/trade/strategies/${id}/pause`)
  },
  cancelStrategy(id) {
    return api.post(`/trade/strategies/${id}/cancel`)
  },
  resetStrategy(id) {
    return api.post(`/trade/strategies/${id}/reset`)
  },
  getStrategyExecutions(id, limit = 50) {
    return api.get(`/trade/strategies/${id}/executions`, { params: { limit } })
  },
  testStrategyTick(id, quote) {
    return api.post(`/trade/strategies/${id}/test-tick`, quote)
  },
  autoTickStrategy(id) {
    return api.post(`/trade/strategies/${id}/auto-tick`)
  },
  getStrategyIntraday(id, { scale = 5, limit = 240 } = {}) {
    return api.get(`/trade/strategies/${id}/intraday`, { params: { scale, limit } })
  },
  getTradeSummary() {
    return api.get('/trade/summary')
  },
  // Broker (live trading)
  getBrokerStatus() {
    return api.get('/trade/broker/status')
  },
  connectBroker() {
    return api.post('/trade/broker/connect')
  },
  disconnectBroker() {
    return api.post('/trade/broker/disconnect')
  },
  getBrokerAccount() {
    return api.get('/trade/broker/account')
  },
  // News & Sentiment
  getStockNews(stockId, { page = 1, page_size = 20 } = {}) {
    return api.get(`/newssentiment/news/${stockId}`, { params: { page, page_size } })
  },
  getStockSentiment(stockId, { days = 7 } = {}) {
    return api.get(`/newssentiment/sentiment/${stockId}`, { params: { days }, timeout: 60000 })
  },
  // Cross-Sectional Quantitative Analysis
  runQuantAnalysis({
    top_n = 30, rebalance_freq = 10, industry_neutral = false,
    preset, industries, exclude_industries, market_cap_min, market_cap_max,
    pe_min, pe_max, pb_min, pb_max, markets,
    backtest_start, backtest_end,
  } = {}) {
    const params = { top_n, rebalance_freq, industry_neutral }
    if (preset) params.preset = preset
    if (industries) params.industries = industries
    if (exclude_industries) params.exclude_industries = exclude_industries
    if (market_cap_min != null) params.market_cap_min = market_cap_min
    if (market_cap_max != null) params.market_cap_max = market_cap_max
    if (pe_min != null) params.pe_min = pe_min
    if (pe_max != null) params.pe_max = pe_max
    if (pb_min != null) params.pb_min = pb_min
    if (pb_max != null) params.pb_max = pb_max
    if (markets) params.markets = markets
    if (backtest_start) params.backtest_start = backtest_start
    if (backtest_end) params.backtest_end = backtest_end
    return api.post('/quant/analyze', null, { params, timeout: 30000 })
  },
  getQuantTaskStatus(taskId) {
    return api.get(`/quant/status/${taskId}`)
  },
  getQuantHistory(limit = 50, offset = 0) {
    return api.get('/quant/history', { params: { limit, offset } })
  },
  getQuantResult(runId) {
    return api.get(`/quant/result/${runId}`)
  },
  updateQuantResult(runId, { name, notes } = {}) {
    return api.put(`/quant/result/${runId}`, { name, notes })
  },
  deleteQuantResult(runId) {
    return api.delete(`/quant/result/${runId}`)
  },
  getQuantFactors() {
    return api.get('/quant/factors')
  },
  getQuantFactorStatus() {
    return api.get('/quant/factor-status')
  },
  getQuantStockProfile(stockId) {
    return api.get(`/quant/stock-profile/${stockId}`, { timeout: 30000 })
  },
  getQuantPresets() {
    return api.get('/quant/presets')
  },
  getQuantIndustries() {
    return api.get('/quant/industries')
  },
  // Quant Auto-Iteration
  toggleQuantIterate(runId, autoIterate) {
    return api.post(`/quant/result/${runId}/iterate`, { auto_iterate: autoIterate })
  },
  getQuantIterations(runId) {
    return api.get(`/quant/result/${runId}/iterations`)
  },
  triggerQuantIteration(runId) {
    return api.post(`/quant/result/${runId}/iterate-now`, null, { timeout: 600000 })
  },
}
