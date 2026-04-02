<script setup>
import { ref, reactive, onMounted, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Download, Delete, Star, StarFilled, Timer, Setting, TrendCharts, InfoFilled, User, Lock, SwitchButton, ArrowDown } from '@element-plus/icons-vue'
import * as echarts from 'echarts'
import api from './api/index.js'

// --- Authentication ---
const isLoggedIn = ref(false)
const currentUser = ref(null)
const loginForm = reactive({ username: '', password: '' })
const loginLoading = ref(false)
const loginError = ref('')

// Change password dialog
const showChangePassword = ref(false)
const changePasswordForm = reactive({ old_password: '', new_password: '', confirm_password: '' })
const changePasswordLoading = ref(false)

// User management (admin)
const userList = ref([])
const userListLoading = ref(false)
const showUserDialog = ref(false)
const userDialogMode = ref('create') // 'create' or 'edit'
const userForm = reactive({ username: '', password: '', display_name: '', role: 'user', permissions: {} })
const userFormLoading = ref(false)
const editingUserId = ref(null)
const allPermissionKeys = ref([])

const permissionLabels = {
  stocks: '自选股管理',
  quotes: '行情数据',
  screener: '智能选股',
  strategy: '策略交易',
  quant: '量化选股',
  logs: '抓取日志',
  schedule: '定时任务',
  config: '配置管理'
}

const isAdmin = computed(() => currentUser.value?.role === 'admin')
const userPermissions = computed(() => {
  if (!currentUser.value) return {}
  if (currentUser.value.role === 'admin') {
    // Admin has all permissions
    const all = {}
    Object.keys(permissionLabels).forEach(k => all[k] = true)
    return all
  }
  return currentUser.value.permissions || {}
})

const hasPermission = (key) => {
  return userPermissions.value[key] === true
}

const doLogin = async () => {
  if (!loginForm.username || !loginForm.password) {
    loginError.value = '请输入用户名和密码'
    return
  }
  loginLoading.value = true
  loginError.value = ''
  try {
    const res = await api.login(loginForm.username, loginForm.password)
    localStorage.setItem('astock_token', res.data.access_token)
    await fetchCurrentUser()
    isLoggedIn.value = true
    loginForm.username = ''
    loginForm.password = ''
    // Load initial data
    loadFavorites()
    loadEnrichedFavorites()
    loadEnrichedStocks()
  } catch (e) {
    loginError.value = e.response?.data?.detail || '登录失败，请检查用户名和密码'
  } finally {
    loginLoading.value = false
  }
}

const fetchCurrentUser = async () => {
  try {
    const res = await api.getMe()
    currentUser.value = res.data
  } catch {
    currentUser.value = null
    isLoggedIn.value = false
    localStorage.removeItem('astock_token')
  }
}

const doLogout = () => {
  localStorage.removeItem('astock_token')
  isLoggedIn.value = false
  currentUser.value = null
  activeMenu.value = 'stocks'
}

// Register the unauthorized callback
api.setOnUnauthorized(() => {
  doLogout()
  ElMessage.warning('登录已过期，请重新登录')
})

const doChangePassword = async () => {
  if (!changePasswordForm.new_password || changePasswordForm.new_password.length < 6) {
    ElMessage.warning('新密码至少6个字符')
    return
  }
  if (changePasswordForm.new_password !== changePasswordForm.confirm_password) {
    ElMessage.warning('两次输入的密码不一致')
    return
  }
  changePasswordLoading.value = true
  try {
    await api.changePassword(changePasswordForm.old_password, changePasswordForm.new_password)
    ElMessage.success('密码修改成功')
    showChangePassword.value = false
    changePasswordForm.old_password = ''
    changePasswordForm.new_password = ''
    changePasswordForm.confirm_password = ''
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '密码修改失败')
  } finally {
    changePasswordLoading.value = false
  }
}

// User management (admin)
const loadUsers = async () => {
  userListLoading.value = true
  try {
    const res = await api.getUsers()
    userList.value = res.data
  } catch (e) {
    ElMessage.error('加载用户列表失败')
  } finally {
    userListLoading.value = false
  }
}

const loadPermissionKeys = async () => {
  try {
    const res = await api.getPermissionKeys()
    allPermissionKeys.value = (res.data.keys || []).map(k => k.key)
  } catch { /* ignore */ }
}

const openCreateUser = () => {
  userDialogMode.value = 'create'
  userForm.username = ''
  userForm.password = ''
  userForm.display_name = ''
  userForm.role = 'user'
  userForm.permissions = {}
  allPermissionKeys.value.forEach(k => { userForm.permissions[k] = true })
  editingUserId.value = null
  showUserDialog.value = true
}

const openEditUser = (user) => {
  userDialogMode.value = 'edit'
  userForm.username = user.username
  userForm.password = ''
  userForm.display_name = user.display_name || ''
  userForm.role = user.role
  userForm.permissions = { ...(user.permissions || {}) }
  editingUserId.value = user.id
  showUserDialog.value = true
}

const saveUser = async () => {
  userFormLoading.value = true
  try {
    if (userDialogMode.value === 'create') {
      if (!userForm.username || !userForm.password) {
        ElMessage.warning('用户名和密码不能为空')
        userFormLoading.value = false
        return
      }
      await api.createUser({
        username: userForm.username,
        password: userForm.password,
        display_name: userForm.display_name || undefined,
        role: userForm.role,
        permissions: userForm.permissions
      })
      ElMessage.success('用户创建成功')
    } else {
      const data = {
        display_name: userForm.display_name || undefined,
        role: userForm.role,
        permissions: userForm.permissions
      }
      if (userForm.password) data.password = userForm.password
      await api.updateUser(editingUserId.value, data)
      ElMessage.success('用户更新成功')
    }
    showUserDialog.value = false
    loadUsers()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  } finally {
    userFormLoading.value = false
  }
}

const deleteUser = async (user) => {
  try {
    await ElMessageBox.confirm(`确定要删除用户 "${user.username}" 吗？`, '确认删除', { type: 'warning' })
    await api.deleteUser(user.id)
    ElMessage.success('用户已删除')
    loadUsers()
  } catch { /* cancelled */ }
}

const toggleUserActive = async (user) => {
  try {
    await api.updateUser(user.id, { is_active: !user.is_active })
    ElMessage.success(user.is_active ? '用户已禁用' : '用户已启用')
    loadUsers()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

// --- Navigation (with browser history support) ---
const activeMenu = ref('stocks')
// Tracks whether navigation was triggered programmatically (e.g. navigateToStock)
let skipPushState = false

const pushMenuState = (menu, extra = {}) => {
  if (!skipPushState) {
    history.pushState({ menu, ...extra }, '', `#${menu}`)
  }
}

const handlePopState = (event) => {
  const state = event.state
  if (state && state.menu) {
    skipPushState = true
    activeMenu.value = state.menu
    // Restore quant sub-tab state
    if (state.menu === 'quant' && state.quantTab) {
      quantTab.value = state.quantTab
      if (state.quantTab === 'history') {
        loadQuantHistory()
      } else if (state.quantTab === 'new' && state.quantRunId) {
        viewQuantResult(state.quantRunId)
      }
    } else {
      // Trigger section-specific data loading
      handleMenuSelect(state.menu)
    }
    skipPushState = false
  }
}

// --- Stock Management ---
const stocks = ref([])
const favorites = ref([])
const stockForm = reactive({ code: '', name: '', market: 'sh' })
const searchKeyword = ref('')
const searchResults = ref([])
const searchLoading = ref(false)
const stocksLoading = ref(false)
const fetchAllLoading = ref(false)
const fetchAllProgress = ref({ current: 0, total: 0, currentStock: '' })
const stockFilterText = ref('')
// Enriched stock data (with profile + latest kline date)
const enrichedStocks = ref([])
const enrichedTotal = ref(0)
const enrichedPage = ref(1)
const enrichedPageSize = ref(50)
const enrichedFavorites = ref([])

const filteredStocks = computed(() => {
  const keyword = stockFilterText.value.trim().toLowerCase()
  if (!keyword) return stocks.value
  return stocks.value.filter(
    (s) =>
      s.code.toLowerCase().includes(keyword) ||
      s.name.toLowerCase().includes(keyword)
  )
})

const loadStocks = async (keyword = '') => {
  stocksLoading.value = true
  try {
    const res = await api.getStocks(false, { keyword, limit: 50 })
    stocks.value = res.data
  } catch (e) {
    ElMessage.error('加载股票列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    stocksLoading.value = false
  }
}

const loadFavorites = async () => {
  try {
    const res = await api.getFavorites()
    favorites.value = res.data
  } catch (e) {
    ElMessage.error('加载自选股失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadEnrichedFavorites = async () => {
  try {
    const res = await api.getFavoritesEnriched()
    enrichedFavorites.value = res.data.items || []
  } catch (e) {
    console.error('Failed to load enriched favorites', e)
  }
}

const loadEnrichedStocks = async () => {
  stocksLoading.value = true
  try {
    const res = await api.getStocksEnriched({
      keyword: stockFilterText.value.trim(),
      page: enrichedPage.value,
      page_size: enrichedPageSize.value,
    })
    enrichedStocks.value = res.data.items || []
    enrichedTotal.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载股票列表失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    stocksLoading.value = false
  }
}

const handleEnrichedPageChange = (page) => {
  enrichedPage.value = page
  loadEnrichedStocks()
}

const handleEnrichedSizeChange = (size) => {
  enrichedPageSize.value = size
  enrichedPage.value = 1
  loadEnrichedStocks()
}

// Debounce stock filter for enriched endpoint
let stockFilterTimer = null
watch(stockFilterText, () => {
  clearTimeout(stockFilterTimer)
  stockFilterTimer = setTimeout(() => {
    enrichedPage.value = 1
    loadEnrichedStocks()
  }, 400)
})

let searchTimer = null
const handleSearch = (query) => {
  if (!query || query.length < 1) {
    searchResults.value = []
    return
  }
  clearTimeout(searchTimer)
  searchTimer = setTimeout(async () => {
    searchLoading.value = true
    try {
      const res = await api.searchStocks(query)
      searchResults.value = res.data || []
    } catch (e) {
      searchResults.value = []
    } finally {
      searchLoading.value = false
    }
  }, 300)
}

const selectSearchResult = (item) => {
  if (item) {
    stockForm.code = item.code || ''
    stockForm.name = item.name || ''
    // Detect market from code prefix or result
    if (item.market) {
      stockForm.market = item.market.toLowerCase()
    } else if (item.code) {
      if (item.code.startsWith('6') || item.code.startsWith('68')) {
        stockForm.market = 'sh'
      } else if (item.code.startsWith('4') || item.code.startsWith('8') || item.code.startsWith('92')) {
        stockForm.market = 'bj'
      } else {
        stockForm.market = 'sz'
      }
    }
  }
}

const addStock = async () => {
  if (!stockForm.code || !stockForm.name) {
    ElMessage.warning('请填写股票代码和名称')
    return
  }
  try {
    await api.addStock({
      code: stockForm.code,
      name: stockForm.name,
      market: stockForm.market,
    })
    ElMessage.success('添加成功')
    stockForm.code = ''
    stockForm.name = ''
    stockForm.market = 'sh'
    searchKeyword.value = ''
    await loadEnrichedStocks()
  } catch (e) {
    ElMessage.error('添加失败: ' + (e.response?.data?.detail || e.message))
  }
}

const toggleFavorite = async (row) => {
  try {
    await api.updateStock(row.id, { is_favorite: !row.is_favorite })
    row.is_favorite = !row.is_favorite
    if (!row.is_favorite) {
      favorites.value = favorites.value.filter((f) => f.id !== row.id)
    }
    ElMessage.success(row.is_favorite ? '已加入自选' : '已移出自选')
    await loadFavorites()
    await loadEnrichedFavorites()
    await loadEnrichedStocks()
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
  }
}

const fetchStock = async (row) => {
  row._fetching = true
  try {
    await api.fetchStock(row.id)
    ElMessage.success(`${row.name} 数据抓取已启动`)
    await Promise.all([loadEnrichedFavorites(), loadEnrichedStocks()])
  } catch (e) {
    ElMessage.error('抓取失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    row._fetching = false
  }
}

const fetchAll = async () => {
  const favStocks = [...enrichedFavorites.value]
  if (!favStocks.length) {
    ElMessage.warning('没有自选股可以抓取')
    return
  }

  fetchAllLoading.value = true
  fetchAllProgress.value = { current: 0, total: favStocks.length, currentStock: '' }

  let successCount = 0
  const errors = []

  for (const stock of favStocks) {
    fetchAllProgress.value.current++
    fetchAllProgress.value.currentStock = stock.name || stock.code

    // Find the live row in enrichedFavorites to set its _fetching flag
    const liveRow = enrichedFavorites.value.find(r => r.id === stock.id)
    try {
      if (liveRow) liveRow._fetching = true
      const res = await api.fetchStockLite(stock.id)
      successCount++
      // Immediately update the row with returned data
      if (liveRow && res.data) {
        if (res.data.latest_kline_date) {
          liveRow.latest_kline_date = res.data.latest_kline_date
        }
      }
    } catch (e) {
      errors.push(`${stock.code}: ${e.response?.data?.detail || e.message}`)
    } finally {
      if (liveRow) liveRow._fetching = false
    }
  }

  fetchAllLoading.value = false

  if (errors.length === 0) {
    ElMessage.success(`全部抓取完成: ${successCount}/${favStocks.length} 成功`)
  } else {
    ElMessage({ type: 'warning', message: `抓取完成: ${successCount} 成功, ${errors.length} 失败`, duration: 5000 })
  }
  // Final refresh to ensure full consistency
  await Promise.all([loadEnrichedFavorites(), loadEnrichedStocks()])
}

const toggleActive = async (row) => {
  try {
    await api.updateStock(row.id, { is_active: !row.is_active })
    ElMessage.success(row.is_active ? '已停用' : '已启用')
    await loadEnrichedStocks()
  } catch (e) {
    ElMessage.error('操作失败: ' + (e.response?.data?.detail || e.message))
  }
}

const deleteStock = async (row) => {
  try {
    await ElMessageBox.confirm(`确定删除 ${row.name} (${row.code})?`, '确认删除', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.deleteStock(row.id)
    ElMessage.success('删除成功')
    await loadFavorites()
    await loadEnrichedStocks()
    await loadEnrichedFavorites()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

// --- Market Data ---
const selectedStockId = ref(null)
const quoteTab = ref('realtime')
const snapshots = ref([])
const klines = ref([])
const snapshotsLoading = ref(false)

// --- Realtime Quote & Intraday Chart (quotes section) ---
const realtimeQuote = ref(null)
const realtimeLoading = ref(false)
let realtimeTimer = null
const realtimeInterval = ref(5000)
const quotesIntradayData = ref([])
const quotesIntradayPrevClose = ref(null)
const quotesIntradayChartRef = ref(null)
let quotesIntradayChart = null
const quotesIntradayLoading = ref(false)
const isTradingTime = ref(false)

/**
 * Check if current time is within A-share trading hours (Beijing time UTC+8).
 * Morning: 9:15 ~ 11:35 (includes pre-open auction + buffer)
 * Afternoon: 12:55 ~ 15:05 (includes closing auction + buffer)
 */
const isAShareTradingTime = () => {
  const now = new Date()
  const utc = now.getTime() + now.getTimezoneOffset() * 60000
  const beijing = new Date(utc + 8 * 3600000)
  const day = beijing.getDay()
  if (day === 0 || day === 6) return false
  const minutes = beijing.getHours() * 60 + beijing.getMinutes()
  return (minutes >= 555 && minutes <= 695) || (minutes >= 775 && minutes <= 905)
}
const klinesLoading = ref(false)
const stockProfile = ref(null)
// Remote stock search for quotes selector
const quoteStockOptions = ref([])
const quoteStockLoading = ref(false)
let quoteSearchTimer = null

const handleQuoteStockSearch = (query) => {
  clearTimeout(quoteSearchTimer)
  if (!query || query.length < 1) {
    // Show a default set when no query (e.g. first 50 stocks)
    if (quoteStockOptions.value.length === 0) {
      loadQuoteStockDefaults()
    }
    return
  }
  quoteSearchTimer = setTimeout(async () => {
    quoteStockLoading.value = true
    try {
      const res = await api.getStocks(false, { keyword: query, limit: 50 })
      quoteStockOptions.value = res.data || []
    } catch (e) {
      console.error('Stock search failed', e)
    } finally {
      quoteStockLoading.value = false
    }
  }, 300)
}

const loadQuoteStockDefaults = async () => {
  if (quoteStockOptions.value.length > 0) return
  quoteStockLoading.value = true
  try {
    const res = await api.getStocks(false, { limit: 50 })
    quoteStockOptions.value = res.data || []
  } catch (e) {
    console.error('Failed to load default stock options', e)
  } finally {
    quoteStockLoading.value = false
  }
}

const loadProfile = async () => {
  if (!selectedStockId.value) { stockProfile.value = null; return }
  try {
    const res = await api.getProfile(selectedStockId.value)
    stockProfile.value = res.data
  } catch {
    stockProfile.value = null
  }
}

const loadSnapshots = async () => {
  if (!selectedStockId.value) return
  snapshotsLoading.value = true
  try {
    const res = await api.getSnapshots(selectedStockId.value)
    snapshots.value = res.data || []
  } catch (e) {
    ElMessage.error('加载快照失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    snapshotsLoading.value = false
  }
}

// --- Realtime Quote Functions (quotes section) ---
const fetchRealtimeQuote = async () => {
  if (!selectedStockId.value) return
  const isFirstLoad = !realtimeQuote.value
  if (isFirstLoad) realtimeLoading.value = true
  try {
    const res = await api.getRealtimeQuote(selectedStockId.value)
    const data = res.data
    // Only accept valid quote data (has price and no error)
    if (data && data.price && !data.error) {
      realtimeQuote.value = data
    } else if (isFirstLoad) {
      // On first load, set even error responses so the UI can show them
      realtimeQuote.value = data
    }
  } catch (e) {
    console.error('Realtime quote fetch failed:', e)
    if (isFirstLoad) {
      // Show a meaningful error instead of blank area
      const msg = e.code === 'ECONNABORTED' ? '请求超时，请稍后重试' : '行情获取失败，请稍后重试'
      realtimeQuote.value = { error: msg }
    }
  } finally {
    if (isFirstLoad) realtimeLoading.value = false
  }
}

const loadQuotesIntradayData = async () => {
  if (!selectedStockId.value) return
  quotesIntradayLoading.value = true
  try {
    const res = await api.getIntradayKlines(selectedStockId.value, { scale: 5, limit: 240 })
    const klineData = res.data.klines || []
    if (klineData.length > 0) {
      quotesIntradayPrevClose.value = klineData[0].open
      let cumAmount = 0
      let cumVolume = 0
      quotesIntradayData.value = klineData.map(k => {
        const timeStr = k.time.includes(' ') ? k.time.split(' ')[1].substring(0, 5) : k.time
        cumAmount += k.close * k.volume
        cumVolume += k.volume
        return {
          time: timeStr,
          close: k.close,
          volume: k.volume,
          avg_price: cumVolume > 0 ? cumAmount / cumVolume : k.close,
        }
      })
    } else {
      quotesIntradayData.value = []
    }
    nextTick(() => renderQuotesIntradayChart())
  } catch (e) {
    console.error('Failed to load intraday data:', e)
  } finally {
    quotesIntradayLoading.value = false
  }
}

const renderQuotesIntradayChart = () => {
  const data = quotesIntradayData.value
  if (!quotesIntradayChartRef.value || data.length === 0) return

  if (!quotesIntradayChart) {
    quotesIntradayChart = echarts.init(quotesIntradayChartRef.value)
  }

  const times = data.map(d => d.time)
  const prices = data.map(d => d.close)
  const avgPrices = data.map(d => d.avg_price)
  const volumes = data.map(d => d.volume)
  const prevClose = quotesIntradayPrevClose.value || prices[0]

  const volColors = data.map((d, i) => {
    if (i === 0) return d.close >= prevClose ? 'rgba(239,83,80,0.6)' : 'rgba(38,166,91,0.6)'
    return d.close >= data[i - 1].close ? 'rgba(239,83,80,0.6)' : 'rgba(38,166,91,0.6)'
  })

  const allPrices = [...prices, ...avgPrices, prevClose]
  const minP = Math.min(...allPrices)
  const maxP = Math.max(...allPrices)
  const pad = (maxP - minP) * 0.15 || 0.1
  const yMin = Math.max(0, minP - pad)
  const yMax = maxP + pad

  const option = {
    animation: false,
    backgroundColor: '#1e1e2f',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(30,30,47,0.95)',
      borderColor: '#444',
      textStyle: { color: '#ddd', fontSize: 12 },
      formatter: (params) => {
        if (!params || params.length === 0) return ''
        const idx = params[0].dataIndex
        const d = data[idx]
        if (!d) return ''
        const pctVal = prevClose > 0 ? ((d.close - prevClose) / prevClose * 100) : 0
        const color = pctVal >= 0 ? '#ef5350' : '#26a65b'
        const pctStr = (pctVal >= 0 ? '+' : '') + pctVal.toFixed(2)
        const vol = d.volume >= 10000 ? (d.volume / 10000).toFixed(1) + '万' : d.volume
        return `<div style="line-height:1.7">
          <div style="font-weight:bold">${d.time}</div>
          <div>价格: <span style="color:${color};font-weight:bold">${d.close.toFixed(2)}</span></div>
          <div>涨跌: <span style="color:${color}">${pctStr}%</span></div>
          <div>均价: <span style="color:#f5c842">${d.avg_price.toFixed(2)}</span></div>
          <div>成交量: ${vol}</div>
        </div>`
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
      label: { backgroundColor: '#333' },
    },
    grid: [
      { left: 60, right: 16, top: 16, height: '58%' },
      { left: 60, right: 16, top: '78%', height: '16%' },
    ],
    xAxis: [
      {
        type: 'category', data: times, gridIndex: 0,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
      {
        type: 'category', data: times, gridIndex: 1,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { color: '#888', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 6)) },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value', gridIndex: 0,
        min: yMin.toFixed(2) * 1, max: yMax.toFixed(2) * 1,
        axisLine: { lineStyle: { color: '#444' } },
        axisLabel: { color: '#aaa', fontSize: 10 },
        splitLine: { lineStyle: { color: '#2a2a40', type: 'dashed' } },
      },
      {
        type: 'value', gridIndex: 1,
        axisLine: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '价格',
        type: 'line',
        xAxisIndex: 0, yAxisIndex: 0,
        data: prices,
        symbol: 'none',
        lineStyle: { width: 1.5, color: '#409eff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(64,158,255,0.25)' },
            { offset: 1, color: 'rgba(64,158,255,0.02)' },
          ]),
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#888', type: 'dashed', width: 1 },
          data: [{ yAxis: prevClose, label: { formatter: prevClose.toFixed(2), color: '#aaa', fontSize: 10 } }],
        },
      },
      {
        name: '均价',
        type: 'line',
        xAxisIndex: 0, yAxisIndex: 0,
        data: avgPrices,
        symbol: 'none',
        lineStyle: { width: 1, color: '#f5c842', type: 'dashed' },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1, yAxisIndex: 1,
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: volColors[i] },
        })),
      },
    ],
  }

  quotesIntradayChart.setOption(option, true)
}

const startRealtimePolling = () => {
  stopRealtimePolling()
  realtimeQuote.value = null
  quotesIntradayData.value = []
  quotesIntradayPrevClose.value = null
  // Always fetch once (shows last known price even after hours)
  loadQuotesIntradayData()
  fetchRealtimeQuote()
  // Only start polling timer during trading hours
  isTradingTime.value = isAShareTradingTime()
  if (isTradingTime.value) {
    realtimeTimer = setInterval(() => {
      fetchRealtimeQuote()
      // Re-check trading hours; stop if market closed
      if (!isAShareTradingTime()) {
        isTradingTime.value = false
        clearInterval(realtimeTimer)
        realtimeTimer = null
      }
    }, realtimeInterval.value)
  }
}

const stopRealtimePolling = () => {
  if (realtimeTimer) {
    clearInterval(realtimeTimer)
    realtimeTimer = null
  }
  // Don't dispose chart here — only on tab switch or stock change
}

const loadKlines = async () => {
  if (!selectedStockId.value) return
  klinesLoading.value = true
  try {
    const res = await api.getKlines(selectedStockId.value)
    klines.value = (res.data || []).sort((a, b) => a.date.localeCompare(b.date))
  } catch (e) {
    ElMessage.error('加载K线失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    klinesLoading.value = false
    await nextTick()
    renderKlineChart()
  }
}

const onStockSelect = () => {
  loadProfile()
  clearAiState()
  newsPage.value = 1
  newsTotal.value = 0
  if (quoteTab.value === 'realtime') {
    startRealtimePolling()
  } else if (quoteTab.value === 'snapshot') {
    loadSnapshots()
  } else if (quoteTab.value === 'kline') {
    loadKlines()
  } else if (quoteTab.value === 'ai-analysis') {
    checkDsaHealth()
    loadAiHistory()
  } else if (quoteTab.value === 'news') {
    loadStockNews(1)
  } else if (quoteTab.value === 'sentiment') {
    loadSentiment()
  }
}

const onTabChange = (tab) => {
  if (tab === 'realtime') {
    startRealtimePolling()
  } else {
    stopRealtimePolling()
    if (quotesIntradayChart) {
      quotesIntradayChart.dispose()
      quotesIntradayChart = null
    }
    if (tab === 'snapshot') {
      loadSnapshots()
    } else if (tab === 'kline') {
      loadKlines()
    } else if (tab === 'ai-analysis') {
      checkDsaHealth()
      if (selectedStockCode.value) {
        loadAiHistory()
      }
    } else if (tab === 'news') {
      loadStockNews(1)
    } else if (tab === 'sentiment') {
      loadSentiment()
    }
  }
}

// --- News & Sentiment ---
const newsData = ref([])
const newsLoading = ref(false)
const newsPage = ref(1)
const newsTotal = ref(0)
const newsPageSize = ref(50)
const sentimentData = ref([])
const sentimentLoading = ref(false)
const sentimentDays = ref(7)

const loadStockNews = async (page) => {
  if (!selectedStockId.value) return
  if (page !== undefined) newsPage.value = page
  newsLoading.value = true
  try {
    const res = await api.getStockNews(selectedStockId.value, { page: newsPage.value, page_size: newsPageSize.value })
    const data = res.data || {}
    newsData.value = data.items || []
    newsTotal.value = data.total || 0
  } catch (e) {
    ElMessage.error('加载公告失败: ' + (e.response?.data?.detail || e.message))
    newsData.value = []
    newsTotal.value = 0
  } finally {
    newsLoading.value = false
  }
}

const loadSentiment = async () => {
  if (!selectedStockId.value) return
  sentimentLoading.value = true
  try {
    const res = await api.getStockSentiment(selectedStockId.value, { days: sentimentDays.value })
    sentimentData.value = res.data || []
    await nextTick()
    renderSentimentChart()
  } catch (e) {
    ElMessage.error('加载评论情绪失败: ' + (e.response?.data?.detail || e.message))
    sentimentData.value = []
  } finally {
    sentimentLoading.value = false
  }
}

const sentimentChartRef = ref(null)
let sentimentChartInstance = null

const renderSentimentChart = () => {
  if (!sentimentChartRef.value || sentimentData.value.length === 0) return
  if (sentimentChartInstance) {
    sentimentChartInstance.dispose()
  }
  sentimentChartInstance = echarts.init(sentimentChartRef.value)
  const dates = sentimentData.value.map(d => d.date)
  const scores = sentimentData.value.map(d => d.sentiment_score)
  const counts = sentimentData.value.map(d => d.comment_count)
  const totalPosts = sentimentData.value.map(d => d.total_posts)

  sentimentChartInstance.setOption({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params) => {
        const idx = params[0]?.dataIndex ?? 0
        const d = sentimentData.value[idx]
        if (!d) return ''
        return `<b>${d.date}</b><br/>
          情绪分数: <b style="color:${d.sentiment_score >= 50 ? '#F56C6C' : '#67C23A'}">${d.sentiment_score}</b><br/>
          独立用户数: ${d.comment_count}<br/>
          帖子总数: ${d.total_posts}<br/>
          平均阅读: ${d.avg_read_count}<br/>
          平均回复: ${d.avg_reply_count}`
      },
    },
    legend: { data: ['情绪分数', '独立用户数', '帖子总数'] },
    grid: { left: '3%', right: '4%', bottom: '12%', containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { rotate: 45, fontSize: 11 },
    },
    yAxis: [
      {
        type: 'value',
        name: '情绪分数',
        min: 0,
        max: 100,
        splitLine: { show: true, lineStyle: { type: 'dashed' } },
        axisLabel: { formatter: '{value}' },
      },
      {
        type: 'value',
        name: '数量',
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', start: 0, end: 100, height: 20, bottom: 0 },
    ],
    series: [
      {
        name: '情绪分数',
        type: 'line',
        data: scores,
        smooth: true,
        lineStyle: { width: 2.5 },
        itemStyle: { color: '#E6A23C' },
        markLine: {
          silent: true,
          data: [{ yAxis: 50, label: { formatter: '中性线', position: 'end' }, lineStyle: { color: '#909399', type: 'dashed' } }],
        },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(230,162,60,0.25)' },
            { offset: 1, color: 'rgba(230,162,60,0.02)' },
          ]),
        },
      },
      {
        name: '独立用户数',
        type: 'bar',
        yAxisIndex: 1,
        data: counts,
        itemStyle: { color: 'rgba(64,158,255,0.6)' },
        barMaxWidth: 20,
      },
      {
        name: '帖子总数',
        type: 'bar',
        yAxisIndex: 1,
        data: totalPosts,
        itemStyle: { color: 'rgba(144,147,153,0.35)' },
        barMaxWidth: 20,
      },
    ],
  })
}


// --- Formatting helpers ---
const marketLabel = (m) => {
  const v = (m || '').toLowerCase()
  if (v === 'sh') return '沪'
  if (v === 'sz') return '深'
  if (v === 'bj') return '京'
  return m || '-'
}

const marketTagType = (m) => {
  const v = (m || '').toLowerCase()
  if (v === 'sh') return 'danger'
  if (v === 'sz') return 'primary'
  if (v === 'bj') return 'success'
  return 'info'
}

const changePctClass = (val) => {
  if (val > 0) return 'price-up'
  if (val < 0) return 'price-down'
  return ''
}

const formatPct = (val) => {
  if (val === null || val === undefined) return '-'
  const prefix = val > 0 ? '+' : ''
  return prefix + Number(val).toFixed(2) + '%'
}

const formatNumber = (val) => {
  if (val === null || val === undefined) return '-'
  return Number(val).toLocaleString()
}

const formatPrice = (val) => {
  if (val === null || val === undefined) return '-'
  return Number(val).toFixed(2)
}

/**
 * Format volume to 手 (1手 = 100股).
 * < 10000手 show as-is, >= 10000手 show as 万手
 */
const formatVolume = (val) => {
  if (val === null || val === undefined) return '-'
  const lots = val / 100 // shares -> 手
  if (lots >= 10000) {
    return (lots / 10000).toFixed(2) + ' 万手'
  }
  return lots.toFixed(0) + ' 手'
}

/**
 * Format amount in 万 or 亿 automatically.
 */
const formatAmount = (val) => {
  if (val === null || val === undefined) return '-'
  if (Math.abs(val) >= 1e8) {
    return (val / 1e8).toFixed(2) + ' 亿'
  }
  if (Math.abs(val) >= 1e4) {
    return (val / 1e4).toFixed(2) + ' 万'
  }
  return val.toFixed(2) + ' 元'
}

// --- Global display timezone ---
const displayTimezone = ref('Asia/Shanghai')

/**
 * Format a datetime string (ISO or raw) in the configured display timezone.
 * @param {string} dt - datetime string (e.g. "2026-03-25T16:00:00" or "2026-03-25 16:00:00")
 * @param {object} opts - { dateOnly: bool, timeOnly: bool }
 */
const formatTz = (dt, opts = {}) => {
  if (!dt) return '-'
  // Backend stores naive UTC datetimes (datetime.utcnow).
  // Append 'Z' so the browser parses them as UTC, not local time.
  let s = String(dt)
  if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s) && !s.endsWith('Z') && !/[+-]\d{2}(:\d{2})?$/.test(s)) {
    s += 'Z'
  }
  const d = new Date(s)
  if (isNaN(d.getTime())) return dt  // fallback: return raw string
  const tz = displayTimezone.value || 'Asia/Shanghai'
  try {
    if (opts.timeOnly) {
      return d.toLocaleTimeString('zh-CN', { timeZone: tz, hour12: false })
    }
    if (opts.dateOnly) {
      return d.toLocaleDateString('zh-CN', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' })
    }
    return d.toLocaleString('zh-CN', { timeZone: tz, hour12: false })
  } catch {
    // invalid timezone fallback
    return d.toLocaleString('zh-CN', { hour12: false })
  }
}

// Load timezone on startup (before login check)
const loadDisplayTimezone = async () => {
  try {
    const res = await api.getConfigSettings()
    displayTimezone.value = res.data?.display_timezone || 'Asia/Shanghai'
  } catch {
    // ignore - use default
  }
}

// --- ECharts K-line ---
let klineChart = null
const klineChartRef = ref(null)

const renderKlineChart = () => {
  const data = computedKlines.value
  if (!klineChartRef.value || data.length === 0) return

  if (klineChart) {
    klineChart.dispose()
  }

  klineChart = echarts.init(klineChartRef.value)

  const dates = data.map((k) => k.date)
  // ECharts candlestick: [open, close, low, high]
  const ohlc = data.map((k) => [k.open, k.close, k.low, k.high])
  const volumes = data.map((k) => k.volume / 100) // convert to 手
  const amounts = data.map((k) => k.amount)
  // Color: red if close >= open (涨), green if close < open (跌)
  const volumeColors = data.map((k) =>
    k.close >= k.open ? 'rgba(239, 83, 80, 0.7)' : 'rgba(38, 166, 91, 0.7)'
  )

  const upColor = '#ef5350'
  const downColor = '#26a65b'

  // Determine the data range to show initially (last 120 items)
  const dataLen = dates.length
  const startPercent = dataLen > 120 ? ((dataLen - 120) / dataLen) * 100 : 0

  // Build MA series for daily timeframe
  const maSeries = []
  const maColors = { 5: '#f5c842', 10: '#42a5f5', 20: '#ab47bc', 30: '#ef5350' }
  const isDaily = klineTimeframe.value === 'daily'
  if (isDaily) {
    for (const period of [5, 10, 20, 30]) {
      const maData = computeMA(data, period)
      maSeries.push({
        name: `MA${period}`,
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: maData,
        smooth: true,
        symbol: 'none',
        lineStyle: { width: 1.2, color: maColors[period] },
      })
    }
  }

  // Timeframe label for tooltip
  const tfLabels = { daily: '日', weekly: '周', monthly: '月', quarterly: '季', yearly: '年' }
  const tfLabel = tfLabels[klineTimeframe.value] || '日'

  const option = {
    animation: false,
    backgroundColor: '#1e1e2f',
    legend: isDaily ? {
      data: ['MA5', 'MA10', 'MA20', 'MA30'],
      top: 4,
      left: 80,
      textStyle: { color: '#aaa', fontSize: 11 },
      itemWidth: 14,
      itemHeight: 2,
    } : undefined,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(30,30,47,0.95)',
      borderColor: '#444',
      textStyle: { color: '#ddd', fontSize: 12 },
      formatter: function (params) {
        if (!params || params.length === 0) return ''
        const idx = params[0].dataIndex
        const k = data[idx]
        if (!k) return ''
        const color = k.close >= k.open ? upColor : downColor
        const pctVal = k.change_pct
        const pctStr = pctVal >= 0 ? '+' + pctVal.toFixed(2) : pctVal.toFixed(2)
        const vol = volumes[idx]
        let volStr = vol >= 10000 ? (vol / 10000).toFixed(2) + ' 万手' : vol.toFixed(0) + ' 手'
        const amt = amounts[idx]
        let amtStr = amt >= 1e8 ? (amt / 1e8).toFixed(2) + ' 亿' : (amt / 1e4).toFixed(2) + ' 万'
        const trStr = k.turnover_rate != null ? k.turnover_rate.toFixed(2) + '%' : '-'
        let maInfo = ''
        if (isDaily) {
          for (const p of params) {
            if (p.seriesName && p.seriesName.startsWith('MA') && p.value != null) {
              maInfo += `<div>${p.seriesName}: <span style="color:${p.color}">${p.value}</span></div>`
            }
          }
        }
        return `<div style="line-height:1.8">
          <div style="font-weight:bold;margin-bottom:4px">${k.date} (${tfLabel}线)</div>
          <div>开盘: <span style="color:${color}">${k.open.toFixed(2)}</span></div>
          <div>收盘: <span style="color:${color};font-weight:bold">${k.close.toFixed(2)}</span></div>
          <div>最高: <span style="color:${upColor}">${k.high.toFixed(2)}</span></div>
          <div>最低: <span style="color:${downColor}">${k.low.toFixed(2)}</span></div>
          <div>涨跌幅: <span style="color:${color}">${pctStr}%</span></div>
          <div>成交量: ${volStr}</div>
          <div>成交额: ${amtStr}</div>
          <div>换手率: ${trStr}</div>
          ${maInfo}
        </div>`
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
      label: { backgroundColor: '#333' },
    },
    grid: [
      { left: 68, right: 48, top: isDaily ? 50 : 30, height: isDaily ? '50%' : '55%' },
      { left: 68, right: 48, top: isDaily ? '68%' : '72%', height: '18%' },
    ],
    xAxis: [
      {
        type: 'category',
        data: dates,
        gridIndex: 0,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: true, lineStyle: { color: '#2a2a40', type: 'dashed' } },
      },
      {
        type: 'category',
        data: dates,
        gridIndex: 1,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { color: '#888', fontSize: 11 },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#aaa', fontSize: 11 },
        splitLine: { lineStyle: { color: '#2a2a40', type: 'dashed' } },
      },
      {
        type: 'value',
        gridIndex: 1,
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: '#888',
          fontSize: 10,
          formatter: function (v) {
            if (v >= 10000) return (v / 10000).toFixed(0) + '万'
            return v.toFixed(0)
          },
        },
        splitLine: { lineStyle: { color: '#2a2a40', type: 'dashed' } },
        splitNumber: 3,
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1],
        start: startPercent,
        end: 100,
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1],
        start: startPercent,
        end: 100,
        top: '94%',
        height: 16,
        borderColor: '#444',
        backgroundColor: '#1e1e2f',
        fillerColor: 'rgba(64,158,255,0.2)',
        handleStyle: { color: '#409eff' },
        textStyle: { color: '#888', fontSize: 10 },
      },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ohlc,
        itemStyle: {
          color: upColor,           // 涨 - 填充色 (收盘>开盘)
          color0: downColor,        // 跌 - 填充色
          borderColor: upColor,     // 涨 - 边框色
          borderColor0: downColor,  // 跌 - 边框色
        },
      },
      ...maSeries,
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: volumeColors[i] },
        })),
      },
    ],
  }

  klineChart.setOption(option)
}

// Resize chart on window resize
const handleResize = () => {
  if (klineChart) klineChart.resize()
}
onMounted(() => window.addEventListener('resize', handleResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  if (klineChart) {
    klineChart.dispose()
    klineChart = null
  }
  stopRealtimePolling()
  if (quotesIntradayChart) {
    quotesIntradayChart.dispose()
    quotesIntradayChart = null
  }
})

// --- Fetch Logs ---
const logs = ref([])
const logsLoading = ref(false)
const deleteLogsLoading = ref(false)

const fetchTypeLabels = {
  'manual_full_fetch': '手动抓取',
  'scheduled_full_fetch': '定时抓取',
  'daily_update_summary': '定时汇总',
  'batch_lite_fetch': '批量抓取',
  'lite': '批量抓取',
  'all': '全量抓取',
  'realtime': '实时行情',
  'kline': 'K线',
}
const fetchTypeLabel = (t) => fetchTypeLabels[t] || t || '-'
const fetchTypeTagType = (t) => {
  if (!t) return ''
  if (t.includes('summary')) return 'warning'
  if (t.includes('scheduled')) return 'success'
  if (t === 'manual_full_fetch') return ''
  if (t === 'batch_lite_fetch' || t === 'lite') return 'info'
  return ''
}

const loadLogs = async () => {
  logsLoading.value = true
  try {
    const res = await api.getLogs()
    logs.value = res.data || []
  } catch (e) {
    ElMessage.error('加载日志失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    logsLoading.value = false
  }
}

const deleteErrorLogs = async () => {
  try {
    await ElMessageBox.confirm('确定要删除所有错误状态的日志记录吗？', '清除错误日志', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch { return }
  deleteLogsLoading.value = true
  try {
    const res = await api.deleteErrorLogs()
    ElMessage.success(res.data?.message || '错误日志已清除')
    await loadLogs()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    deleteLogsLoading.value = false
  }
}

const deleteAllLogs = async () => {
  try {
    await ElMessageBox.confirm('确定要删除全部日志记录吗？此操作不可恢复。', '清除所有日志', {
      confirmButtonText: '全部删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch { return }
  deleteLogsLoading.value = true
  try {
    const res = await api.deleteAllLogs()
    ElMessage.success(res.data?.message || '所有日志已清除')
    await loadLogs()
  } catch (e) {
    ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    deleteLogsLoading.value = false
  }
}

// --- Menu Navigation ---
const handleMenuSelect = (key) => {
  activeMenu.value = key
  pushMenuState(key)
  if (key === 'stocks') {
    loadFavorites()
  } else if (key === 'quotes') {
    loadQuoteStockDefaults() // load first 50 stocks for selector
  } else if (key === 'logs') {
    loadLogs()
  } else if (key === 'screener') {
    loadPresets()
    loadIndustries()
  } else if (key === 'schedule') {
    loadScheduleSettings()
    loadScheduleStatus()
  } else if (key === 'config') {
    loadConfigSettings()
  } else if (key === 'strategy') {
    loadStrategies()
  } else if (key === 'quant') {
    loadQuantPresets()
  } else if (key === 'users') {
    loadUsers()
    loadPermissionKeys()
  }
}

// --- Screener ---
const presets = ref([])
const industries = ref([])
const screenResults = ref([])
const screenTotal = ref(0)
const screenLoading = ref(false)
const activePreset = ref('')
const screenPage = ref(1)
const screenPageSize = ref(50)

// Column visibility control — keys match column prop names
const screenColDefs = [
  { key: 'code', label: '代码', default: true, fixed: true },
  { key: 'name', label: '名称', default: true, fixed: true },
  { key: 'industry', label: '行业', default: true },
  { key: 'latest_close', label: '最新价', default: true },
  { key: 'total_market_cap', label: '总市值', default: true },
  { key: 'pe_ttm', label: 'PE(TTM)', default: true },
  { key: 'pb', label: 'PB', default: true },
  { key: 'price_percentile', label: '价格百分位', default: true },
  { key: 'volume_surge_ratio', label: '量比', default: true },
  { key: 'weekly_change_pct', label: '周涨跌', default: true },
  // volume pattern columns
  { key: 'volume_pattern_score', label: '形态评分', default: true, vp: true },
  { key: 'surge_weeks_count', label: '放量周数', default: true, vp: true },
  { key: 'max_surge_vol_ratio', label: '放量倍数', default: true, vp: true },
  { key: 'surge_price_gain', label: '放量涨幅', default: true, vp: true },
  { key: 'pullback_weeks', label: '回调周数', default: true, vp: true },
  { key: 'pullback_pct', label: '回调幅度', default: true, vp: true },
  { key: 'recent_5d_vs_minweek', label: '5日/周最低', default: true, vp: true },
  { key: 'latest_vs_minday', label: '最新日/日最低', default: true, vp: true },
  { key: 'recent_vol_percentile', label: '近期量位', default: false, vp: true },
  { key: 'base_weekly_vol', label: '周基准量', default: false, vp: true },
  { key: 'pullback_min_weekly_vol', label: '回调最低周量', default: false, vp: true },
  { key: 'min_daily_vol', label: '期间日最低量', default: false, vp: true },
]
const screenVisibleCols = ref(screenColDefs.filter(c => c.default).map(c => c.key))

const showCol = (key) => screenVisibleCols.value.includes(key)

const paginatedScreenResults = computed(() => {
  const start = (screenPage.value - 1) * screenPageSize.value
  return screenResults.value.slice(start, start + screenPageSize.value)
})

const screenForm = reactive({
  industries: [],
  exclude_industries: [],
  min_market_cap: null,
  max_market_cap: null,
  min_pe: null,
  max_pe: null,
  min_pb: null,
  max_pb: null,
  max_price_percentile: 60,
  min_volume_surge_ratio: null,
  enable_volume_pattern: false,
  sort_by: 'market_cap_desc',
  limit: 500,
  // Volume pattern customizable parameters
  vp_lookback_months: 4,
  vp_min_surge_weeks: 3,
  vp_surge_vol_ratio: 1.4,
  vp_min_surge_gain: 10,
  vp_max_surge_gain: 30,
  vp_min_pullback_pct: 10,
  vp_min_pullback_weeks: 2,
  vp_recent_vol_days: 5,
  vp_recent_5d_vs_minweek_max: 1.1,
  vp_latest_vs_minday_max: 1.1,
  vp_min_score: 30,
})

const loadPresets = async () => {
  try {
    const res = await api.getPresets()
    presets.value = res.data || []
  } catch (e) {
    console.error('Failed to load presets', e)
  }
}

const loadIndustries = async () => {
  try {
    const res = await api.getIndustries()
    industries.value = res.data || []
  } catch (e) {
    console.error('Failed to load industries', e)
  }
}

// Industry +/- mode helpers (shared by screener and quant)
const screenIndustryTemp = ref('')
const addScreenIndustry = (val) => {
  if (!val) return
  if (!screenForm.industries.includes(val) && !screenForm.exclude_industries.includes(val)) {
    screenForm.industries.push(val)
  }
  screenIndustryTemp.value = ''
}
const toggleScreenIndustry = (ind) => {
  if (screenForm.industries.includes(ind)) {
    screenForm.industries = screenForm.industries.filter(i => i !== ind)
    screenForm.exclude_industries.push(ind)
  } else if (screenForm.exclude_industries.includes(ind)) {
    screenForm.exclude_industries = screenForm.exclude_industries.filter(i => i !== ind)
    screenForm.industries.push(ind)
  }
}
const removeScreenIndustry = (ind) => {
  screenForm.industries = screenForm.industries.filter(i => i !== ind)
  screenForm.exclude_industries = screenForm.exclude_industries.filter(i => i !== ind)
}
const screenIndustryAvailable = computed(() => {
  const selected = new Set([...screenForm.industries, ...screenForm.exclude_industries])
  return industries.value.filter(i => !selected.has(i))
})

const runPresetScreen = async (key) => {
  activePreset.value = key
  screenLoading.value = true
  screenPage.value = 1
  try {
    const res = await api.runPreset(key, 500)
    screenResults.value = res.data.results || []
    screenTotal.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('选股失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    screenLoading.value = false
  }
}

const runCustomScreen = async () => {
  activePreset.value = ''
  screenLoading.value = true
  screenPage.value = 1
  try {
    const criteria = {}
    if (screenForm.industries.length) criteria.industries = screenForm.industries
    if (screenForm.exclude_industries.length) criteria.exclude_industries = screenForm.exclude_industries
    if (screenForm.min_market_cap) criteria.min_market_cap = Number(screenForm.min_market_cap)
    if (screenForm.max_market_cap) criteria.max_market_cap = Number(screenForm.max_market_cap)
    if (screenForm.min_pe) criteria.min_pe = Number(screenForm.min_pe)
    if (screenForm.max_pe) criteria.max_pe = Number(screenForm.max_pe)
    if (screenForm.min_pb) criteria.min_pb = Number(screenForm.min_pb)
    if (screenForm.max_pb) criteria.max_pb = Number(screenForm.max_pb)
    if (screenForm.max_price_percentile) {
      criteria.max_price_percentile = Number(screenForm.max_price_percentile)
      criteria.price_percentile_days = 250
    }
    if (screenForm.min_volume_surge_ratio) {
      criteria.min_volume_surge_ratio = Number(screenForm.min_volume_surge_ratio)
    }
    if (screenForm.enable_volume_pattern) {
      criteria.enable_volume_pattern = true
      criteria.vp_lookback_months = Number(screenForm.vp_lookback_months) || 4
      criteria.vp_min_surge_weeks = Number(screenForm.vp_min_surge_weeks) || 3
      criteria.vp_surge_vol_ratio = Number(screenForm.vp_surge_vol_ratio) || 1.4
      criteria.vp_min_surge_gain = Number(screenForm.vp_min_surge_gain) || 10
      criteria.vp_max_surge_gain = Number(screenForm.vp_max_surge_gain) || 30
      criteria.vp_min_pullback_pct = Number(screenForm.vp_min_pullback_pct) || 10
      criteria.vp_min_pullback_weeks = Number(screenForm.vp_min_pullback_weeks) || 2
      criteria.vp_recent_vol_days = Number(screenForm.vp_recent_vol_days) || 5
      criteria.vp_recent_5d_vs_minweek_max = Number(screenForm.vp_recent_5d_vs_minweek_max) || 1.1
      criteria.vp_latest_vs_minday_max = Number(screenForm.vp_latest_vs_minday_max) || 1.1
      criteria.vp_min_score = Number(screenForm.vp_min_score) || 30
    }
    criteria.sort_by = screenForm.sort_by
    // When volume pattern is enabled, default to sorting by pattern score
    if (screenForm.enable_volume_pattern && criteria.sort_by === 'market_cap_desc') {
      criteria.sort_by = 'pattern_score_desc'
    }
    criteria.limit = screenForm.limit || 50
    const res = await api.runScreen(criteria)
    screenResults.value = res.data.results || []
    screenTotal.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('选股失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    screenLoading.value = false
  }
}

const formatMcap = (v) => {
  if (!v) return '-'
  return v >= 10000 ? (v / 10000).toFixed(1) + '万亿' : v.toFixed(1) + '亿'
}

const formatPctValue = (v) => {
  if (v === null || v === undefined) return '-'
  return Number(v).toFixed(1) + '%'
}

const formatRatio = (v) => {
  if (v === null || v === undefined) return '-'
  return Number(v).toFixed(2) + 'x'
}

// Navigate from screener result to quotes section
const navigateToStock = async (row) => {
  // Try enriched stocks, enriched favorites, and quote options
  let stock = enrichedStocks.value.find((s) => s.code === row.code)
    || enrichedFavorites.value.find((s) => s.code === row.code)
    || quoteStockOptions.value.find((s) => s.code === row.code)
  if (!stock) {
    // Fetch from server by keyword
    try {
      const res = await api.getStocks(false, { keyword: row.code, limit: 1 })
      if (res.data && res.data.length > 0) {
        stock = res.data[0]
        // Add to quote options so the selector can display it
        quoteStockOptions.value = [stock, ...quoteStockOptions.value]
      }
    } catch (e) {
      // ignore
    }
  }
  if (stock) {
    selectedStockId.value = stock.id
    // Ensure stock is in the quote options for display
    if (!quoteStockOptions.value.find((s) => s.id === stock.id)) {
      quoteStockOptions.value = [stock, ...quoteStockOptions.value]
    }
    activeMenu.value = 'quotes'
    pushMenuState('quotes', { stockId: stock.id })
    loadProfile()
    // Default to kline tab
    quoteTab.value = 'kline'
    loadKlines()
  } else {
    ElMessage.warning(`股票 ${row.code} 未找到`)
  }
}

// --- K-line timeframe ---
const klineTimeframe = ref('daily')  // daily, weekly, monthly, quarterly, yearly

// Compute aggregated kline data based on timeframe
const computedKlines = computed(() => {
  if (!klines.value.length) return []
  if (klineTimeframe.value === 'daily') return klines.value

  // Group by period
  const groups = new Map()
  for (const k of klines.value) {
    const d = new Date(k.date)
    let key
    if (klineTimeframe.value === 'weekly') {
      // ISO week: get Monday of the week
      const day = d.getDay() || 7 // Sunday = 7
      const monday = new Date(d)
      monday.setDate(d.getDate() - day + 1)
      key = monday.toISOString().slice(0, 10)
    } else if (klineTimeframe.value === 'monthly') {
      key = k.date.slice(0, 7) // YYYY-MM
    } else if (klineTimeframe.value === 'quarterly') {
      const q = Math.floor(d.getMonth() / 3) + 1
      key = `${d.getFullYear()}-Q${q}`
    } else if (klineTimeframe.value === 'yearly') {
      key = `${d.getFullYear()}`
    }
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(k)
  }

  // Aggregate each group
  const result = []
  for (const [key, bars] of groups) {
    if (!bars.length) continue
    const first = bars[0]
    const last = bars[bars.length - 1]
    result.push({
      date: last.date,
      open: first.open,
      close: last.close,
      high: Math.max(...bars.map((b) => b.high)),
      low: Math.min(...bars.map((b) => b.low)),
      volume: bars.reduce((s, b) => s + (b.volume || 0), 0),
      amount: bars.reduce((s, b) => s + (b.amount || 0), 0),
      change_pct: first.open > 0 ? ((last.close - first.open) / first.open) * 100 : 0,
      turnover_rate: bars.reduce((s, b) => s + (b.turnover_rate || 0), 0),
    })
  }
  return result
})

// Compute MA lines (only for daily)
const computeMA = (data, period) => {
  const result = []
  for (let i = 0; i < data.length; i++) {
    if (i < period - 1) {
      result.push(null)
    } else {
      let sum = 0
      for (let j = i - period + 1; j <= i; j++) {
        sum += data[j].close
      }
      result.push(+(sum / period).toFixed(2))
    }
  }
  return result
}

const onTimeframeChange = () => {
  nextTick(() => renderKlineChart())
}

// --- Schedule Settings ---
const scheduleSettings = reactive({
  daily_update_hour: 16,
  daily_update_minute: 0,
  daily_update_timezone: 'Asia/Shanghai',
  daily_update_enabled: true,
  daily_update_last_run: null,
  daily_update_last_status: null,
  daily_update_last_message: null,
})
const scheduleStatus = ref(null)
const scheduleLoading = ref(false)
const scheduleSaving = ref(false)
const triggerLoading = ref(false)

const commonTimezones = [
  'Asia/Shanghai',
  'Asia/Hong_Kong',
  'Asia/Tokyo',
  'Asia/Singapore',
  'America/New_York',
  'America/Chicago',
  'Europe/London',
  'UTC',
]

const loadScheduleSettings = async () => {
  scheduleLoading.value = true
  try {
    const res = await api.getScheduleSettings()
    Object.assign(scheduleSettings, res.data)
  } catch (e) {
    console.error('Failed to load schedule settings', e)
  } finally {
    scheduleLoading.value = false
  }
}

const loadScheduleStatus = async () => {
  try {
    const res = await api.getScheduleStatus()
    scheduleStatus.value = res.data
  } catch (e) {
    console.error('Failed to load schedule status', e)
  }
}

const saveScheduleSettings = async () => {
  scheduleSaving.value = true
  try {
    const res = await api.updateScheduleSettings({
      daily_update_hour: scheduleSettings.daily_update_hour,
      daily_update_minute: scheduleSettings.daily_update_minute,
      daily_update_timezone: scheduleSettings.daily_update_timezone,
      daily_update_enabled: scheduleSettings.daily_update_enabled,
    })
    Object.assign(scheduleSettings, res.data)
    ElMessage.success('定时任务设置已保存')
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    scheduleSaving.value = false
  }
}

const triggerManualUpdate = async () => {
  try {
    await ElMessageBox.confirm(
      '确定立即执行每日数据更新？这可能需要较长时间。',
      '确认手动触发',
      { confirmButtonText: '立即执行', cancelButtonText: '取消', type: 'warning' }
    )
  } catch { return }
  triggerLoading.value = true
  try {
    await api.triggerDailyUpdate()
    ElMessage.success('每日更新任务已启动，请稍后查看状态')
    // Poll status after a short delay
    setTimeout(loadScheduleStatus, 3000)
  } catch (e) {
    ElMessage.error('触发失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    triggerLoading.value = false
  }
}

const formatScheduleTime = (h, m) => {
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

const statusTagType = (status) => {
  if (status === 'success') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'running') return 'primary'
  if (status === 'failed') return 'danger'
  return 'info'
}

// --- AI Analysis (DSA Integration) ---
const aiReport = ref(null)
const aiLoading = ref(false)
const aiTaskId = ref(null)
const aiError = ref('')
const aiDsaReachable = ref(null) // null = unknown, true/false
const aiHistory = ref([])
const aiHistoryLoading = ref(false)
const aiShowDetails = ref(false)
let aiPollTimer = null

const selectedStockCode = computed(() => {
  if (!selectedStockId.value) return null
  const stock = quoteStockOptions.value.find((s) => s.id === selectedStockId.value)
  return stock ? stock.code : null
})

const selectedStockName = computed(() => {
  if (!selectedStockId.value) return null
  const stock = quoteStockOptions.value.find((s) => s.id === selectedStockId.value)
  return stock ? stock.name : null
})

const sentimentColor = (score) => {
  if (score == null) return '#909399'
  if (score <= 20) return '#67c23a'   // 极度悲观 → 绿色(跌)
  if (score <= 40) return '#e6a23c'
  if (score <= 60) return '#909399'
  if (score <= 80) return '#f56c6c'   // 乐观 → 红色(涨)
  return '#409eff'
}

const sentimentLabel = (score) => {
  if (score == null) return '-'
  if (score <= 20) return '极度悲观'
  if (score <= 40) return '悲观'
  if (score <= 60) return '中性'
  if (score <= 80) return '乐观'
  return '极度乐观'
}

const adviceTagType = (advice) => {
  if (!advice) return 'info'
  if (advice.includes('买') || advice.includes('加')) return 'danger'
  if (advice.includes('持')) return 'warning'
  if (advice.includes('卖') || advice.includes('减')) return 'success'
  return 'info'
}

const trendIcon = (trend) => {
  if (!trend) return ''
  if (trend.includes('多') || trend.includes('涨') || trend.includes('Bullish')) return '↑'
  if (trend.includes('空') || trend.includes('跌') || trend.includes('Bearish')) return '↓'
  return '→'
}

const clearAiState = () => {
  aiReport.value = null
  aiError.value = ''
  aiTaskId.value = null
  aiHistory.value = []
  if (aiPollTimer) { clearInterval(aiPollTimer); aiPollTimer = null }
}

const checkDsaHealth = async () => {
  try {
    const res = await api.aiHealth()
    aiDsaReachable.value = res.data.reachable
  } catch {
    aiDsaReachable.value = false
  }
}

const loadAiHistory = async () => {
  if (!selectedStockCode.value) return
  aiHistoryLoading.value = true
  try {
    const res = await api.aiHistory(selectedStockCode.value, 5)
    aiHistory.value = res.data || []
  } catch {
    aiHistory.value = []
  } finally {
    aiHistoryLoading.value = false
  }
}

const loadAiReport = async (recordId) => {
  aiLoading.value = true
  aiError.value = ''
  try {
    const res = await api.aiReport(recordId)
    aiReport.value = res.data
  } catch (e) {
    aiError.value = '加载报告失败: ' + (e.response?.data?.detail || e.message)
  } finally {
    aiLoading.value = false
  }
}

const startAiAnalysis = async (forceRefresh = false) => {
  if (!selectedStockCode.value) return
  aiLoading.value = true
  aiError.value = ''
  aiReport.value = null

  try {
    const res = await api.aiAnalyze(selectedStockCode.value, selectedStockName.value, { forceRefresh })
    const data = res.data

    if (data.status === 'completed' && data.report) {
      aiReport.value = data.report
      aiLoading.value = false
      loadAiHistory()
      return
    }

    if (data.task_id) {
      aiTaskId.value = data.task_id
      pollAiStatus()
    } else {
      aiError.value = data.message || '未知状态'
      aiLoading.value = false
    }
  } catch (e) {
    aiError.value = e.response?.data?.detail || e.message
    aiLoading.value = false
  }
}

const pollAiStatus = () => {
  if (aiPollTimer) clearInterval(aiPollTimer)
  aiPollTimer = setInterval(async () => {
    if (!aiTaskId.value) { clearInterval(aiPollTimer); aiPollTimer = null; return }
    try {
      const res = await api.aiStatus(aiTaskId.value)
      const data = res.data
      if (data.status === 'completed' && data.report) {
        aiReport.value = data.report
        aiLoading.value = false
        aiTaskId.value = null
        clearInterval(aiPollTimer)
        aiPollTimer = null
        loadAiHistory()
      } else if (data.status === 'failed') {
        aiError.value = data.error || '分析失败'
        aiLoading.value = false
        aiTaskId.value = null
        clearInterval(aiPollTimer)
        aiPollTimer = null
      }
      // else still pending/processing, keep polling
    } catch (e) {
      aiError.value = '状态查询失败: ' + e.message
      aiLoading.value = false
      clearInterval(aiPollTimer)
      aiPollTimer = null
    }
  }, 3000)
}

// --- Configuration Management ---
const configLoading = ref(false)
const configSaving = ref(false)
const configTestLoading = ref(false)
const configTestResult = ref(null)
const showApiKey = ref(false)

const configForm = reactive({
  llm: {
    model: '',
    api_key: '',
    api_url: '',
    temperature: 0.7,
    max_tokens: 8192,
    request_timeout: 120,
    ssl_verify: true,
  },
  data_source: {
    priority: '',
    timeout: 10,
    tushare_token: '',
  },
  service_ports: {
    backend: 8000,
    frontend: 5174,
    postgres: 5432,
    redis: 6379,
    grafana: 3000,
  },
  broker: {
    account: '',
    password: '',
    qmt_path: '',
  },
  display_timezone: 'Asia/Shanghai',
})

// Track the masked key from the server so we know if user typed a new one
let originalMaskedKey = ''
let configBrokerMasked = ref('')
const configBrokerXtInstalled = ref(false)

// LLM provider presets for quick configuration
const llmProviderPresets = [
  { label: '自定义 (OpenAI 兼容)', value: 'custom', model: '', url: '', hint: '填写任意 OpenAI 兼容端点' },
  { label: 'DeepSeek 深度求索', value: 'deepseek', model: 'deepseek/deepseek-chat', url: '', hint: '原生支持，无需填 URL。去 platform.deepseek.com 申请 Key' },
  { label: 'DeepSeek (OpenAI 兼容)', value: 'deepseek_openai', model: 'openai/deepseek-chat', url: 'https://api.deepseek.com/v1', hint: 'OpenAI 兼容模式' },
  { label: '通义千问 Qwen (DashScope)', value: 'dashscope', model: 'dashscope/qwen-plus', url: '', hint: '原生支持。去 dashscope.console.aliyun.com 申请 Key' },
  { label: '月之暗面 Moonshot/Kimi', value: 'moonshot', model: 'moonshot/moonshot-v1-8k', url: '', hint: '原生支持。去 platform.moonshot.cn 申请 Key' },
  { label: '豆包 Doubao (火山引擎)', value: 'volcengine', model: 'volcengine/doubao-seed-2-0-pro-260215', url: '', hint: '原生支持。需在火山引擎创建推理接入点' },
  { label: '智谱 GLM (OpenAI 兼容)', value: 'zhipu', model: 'openai/glm-4-flash', url: 'https://open.bigmodel.cn/api/paas/v4', hint: '去 open.bigmodel.cn 申请 Key' },
  { label: '硅基流动 SiliconFlow', value: 'siliconflow', model: 'openai/Qwen/Qwen3-8B', url: 'https://api.siliconflow.cn/v1', hint: '聚合平台，多模型可选。去 siliconflow.cn 申请 Key' },
  { label: '零一万物 Yi (01.AI)', value: 'yi', model: 'openai/yi-large', url: 'https://api.lingyiwanwu.com/v1', hint: '去 platform.lingyiwanwu.com 申请 Key' },
  { label: '阶跃星辰 Stepfun', value: 'stepfun', model: 'openai/step-2-16k', url: 'https://api.stepfun.com/v1', hint: '去 platform.stepfun.com 申请 Key' },
  { label: 'OpenAI 官方', value: 'openai', model: 'openai/gpt-4o-mini', url: '', hint: '去 platform.openai.com 申请 Key，URL 留空' },
  { label: 'Google Gemini', value: 'gemini', model: 'gemini/gemini-2.5-flash', url: '', hint: '去 aistudio.google.com 申请 Key' },
]
const selectedLLMPreset = ref('custom')

function applyLLMPreset(val) {
  const p = llmProviderPresets.find(x => x.value === val)
  if (!p || val === 'custom') return
  configForm.llm.model = p.model
  configForm.llm.api_url = p.url
  // Public cloud providers use valid SSL certs — enable verification
  configForm.llm.ssl_verify = true
  // Don't touch api_key — user fills that in themselves
}

const loadConfigSettings = async () => {
  configLoading.value = true
  configTestResult.value = null
  try {
    const res = await api.getConfigSettings()
    const d = res.data
    configForm.llm.model = d.llm.model || ''
    configForm.llm.api_key = ''  // never pre-fill real key
    originalMaskedKey = d.llm.api_key_masked || ''
    configForm.llm.api_url = d.llm.api_url || ''
    configForm.llm.temperature = d.llm.temperature
    configForm.llm.max_tokens = d.llm.max_tokens
    configForm.llm.request_timeout = d.llm.request_timeout
    configForm.llm.ssl_verify = d.llm.ssl_verify
    // Auto-detect current provider preset from saved model string
    const m = d.llm.model || ''
    const matched = llmProviderPresets.find(p => p.value !== 'custom' && p.model && m === p.model)
    selectedLLMPreset.value = matched ? matched.value : 'custom'
    configForm.data_source.priority = d.data_source.priority || ''
    configForm.data_source.timeout = d.data_source.timeout
    configForm.data_source.tushare_token = ''  // never pre-fill token
    configForm.service_ports.backend = d.service_ports.backend
    configForm.service_ports.frontend = d.service_ports.frontend
    configForm.service_ports.postgres = d.service_ports.postgres
    configForm.service_ports.redis = d.service_ports.redis
    configForm.service_ports.grafana = d.service_ports.grafana
    showApiKey.value = false
    // Broker settings
    configForm.broker.account = ''  // never pre-fill account
    configForm.broker.password = ''
    configForm.broker.qmt_path = d.broker?.qmt_path || ''
    configBrokerMasked.value = d.broker?.account_masked || ''
    configBrokerXtInstalled.value = d.broker?.xtquant_installed || false
    // Display timezone
    configForm.display_timezone = d.display_timezone || 'Asia/Shanghai'
    displayTimezone.value = configForm.display_timezone
  } catch (e) {
    ElMessage.error('加载配置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    configLoading.value = false
  }
}

const saveConfigSettings = async () => {
  configSaving.value = true
  try {
    const payload = {
      llm: {
        model: configForm.llm.model || null,
        api_url: configForm.llm.api_url || null,
        temperature: configForm.llm.temperature,
        max_tokens: configForm.llm.max_tokens,
        request_timeout: configForm.llm.request_timeout,
        ssl_verify: configForm.llm.ssl_verify,
      },
      data_source: {
        priority: configForm.data_source.priority || null,
        timeout: configForm.data_source.timeout,
      },
      service_ports: {
        backend: configForm.service_ports.backend,
        frontend: configForm.service_ports.frontend,
        postgres: configForm.service_ports.postgres,
        redis: configForm.service_ports.redis,
        grafana: configForm.service_ports.grafana,
      },
    }
    // Only send api_key if user actually typed a new one
    if (configForm.llm.api_key) {
      payload.llm.api_key = configForm.llm.api_key
    }
    // Only send tushare_token if changed
    if (configForm.data_source.tushare_token) {
      payload.data_source.tushare_token = configForm.data_source.tushare_token
    }
    // Broker settings — only send non-empty values
    const brokerPayload = {}
    if (configForm.broker.account) brokerPayload.account = configForm.broker.account
    if (configForm.broker.password) brokerPayload.password = configForm.broker.password
    if (configForm.broker.qmt_path) brokerPayload.qmt_path = configForm.broker.qmt_path
    if (Object.keys(brokerPayload).length > 0) {
      payload.broker = brokerPayload
    }
    // Display timezone
    payload.display_timezone = configForm.display_timezone
    await api.updateConfigSettings(payload)
    ElMessage.success('配置已保存')
    await loadConfigSettings()
  } catch (e) {
    ElMessage.error('保存配置失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    configSaving.value = false
  }
}

const testLLMConnection = async () => {
  configTestLoading.value = true
  configTestResult.value = null
  try {
    const res = await api.testLLM()
    configTestResult.value = res.data
  } catch (e) {
    configTestResult.value = {
      success: false,
      message: e.response?.data?.detail || e.message,
    }
  } finally {
    configTestLoading.value = false
  }
}

// --- Strategy Trading ---
const strategies = ref([])
const strategyLoading = ref(false)
const showStrategyDialog = ref(false)
const editingStrategy = ref(null)
const strategyDetailId = ref(null)
const strategyDetail = ref(null)
const strategyExecs = ref([])
const strategyExecsLoading = ref(false)
const testTickPrice = ref(0)
const testTickLoading = ref(false)
const autoTickTimer = ref(null)
const autoTickLoading = ref(false)
const liveQuote = ref(null)
const autoTickInterval = ref(5000)  // 5 seconds
const intradayChartRef = ref(null)
let intradayChart = null
const intradayData = ref([])        // { time, close, volume, avg_price }
const intradayPrevClose = ref(null) // previous day close for baseline

const CONDITION_FIELDS = [
  { value: 'price', label: '当前价格' },
  { value: 'open_price', label: '开盘价格' },
  { value: 'change_pct', label: '涨跌幅%' },
  { value: 'rise_pct', label: '涨幅% (较昨收)' },
  { value: 'fall_pct', label: '跌幅% (较昨收)' },
  { value: 'profit_pct', label: '浮盈比例%' },
  { value: 'loss_pct', label: '浮亏比例%' },
  { value: 'volume', label: '成交量' },
  { value: 'turnover_rate', label: '换手率%' },
]
const CONDITION_OPS = ['>=', '<=', '>', '<', '==']

const strategyForm = reactive({
  name: '',
  stock_code: '',
  stock_name: '',
  market: 'SH',
  mode: 'simulated',
  sim_initial_cash: 100000,
  notes: '',
  steps: [],
})

const strategySearchResults = ref([])
const strategySearchLoading = ref(false)
let strategySearchTimer = null

const handleStrategySearch = (query) => {
  if (!query || query.length < 1) {
    strategySearchResults.value = []
    return
  }
  clearTimeout(strategySearchTimer)
  strategySearchTimer = setTimeout(async () => {
    strategySearchLoading.value = true
    try {
      const res = await api.searchStocks(query)
      strategySearchResults.value = res.data || []
    } catch (e) {
      strategySearchResults.value = []
    } finally {
      strategySearchLoading.value = false
    }
  }, 300)
}

const selectStrategyStock = (item) => {
  if (item) {
    strategyForm.stock_code = item.code || ''
    strategyForm.stock_name = item.name || ''
    if (item.market) {
      strategyForm.market = item.market.toUpperCase()
    } else if (item.code) {
      if (item.code.startsWith('6') || item.code.startsWith('68')) {
        strategyForm.market = 'SH'
      } else if (item.code.startsWith('4') || item.code.startsWith('8') || item.code.startsWith('92')) {
        strategyForm.market = 'BJ'
      } else {
        strategyForm.market = 'SZ'
      }
    }
  }
}

const addStep = () => {
  strategyForm.steps.push({
    name: `步骤${strategyForm.steps.length + 1}`,
    condition_logic: 'AND',
    action_type: 'buy',
    quantity: 100,
    price_type: 'market',
    limit_price: null,
    conditions: [{ field: 'price', operator: '<=', value: 0 }],
  })
}

const removeStep = (idx) => {
  strategyForm.steps.splice(idx, 1)
}

const addCondition = (step) => {
  step.conditions.push({ field: 'price', operator: '<=', value: 0 })
}

const removeCondition = (step, idx) => {
  step.conditions.splice(idx, 1)
}

const resetStrategyForm = () => {
  strategyForm.name = ''
  strategyForm.stock_code = ''
  strategyForm.stock_name = ''
  strategyForm.market = 'SH'
  strategyForm.mode = 'simulated'
  strategyForm.sim_initial_cash = 100000
  strategyForm.notes = ''
  strategyForm.steps = []
  editingStrategy.value = null
}

const openCreateStrategy = () => {
  resetStrategyForm()
  showStrategyDialog.value = true
}

const openEditStrategy = (s) => {
  editingStrategy.value = s
  strategyForm.name = s.name
  strategyForm.stock_code = s.stock_code
  strategyForm.stock_name = s.stock_name
  strategyForm.market = s.market
  strategyForm.mode = s.mode
  strategyForm.sim_initial_cash = s.sim_initial_cash
  strategyForm.notes = s.notes || ''
  strategyForm.steps = (s.steps || []).map(st => ({
    name: st.name,
    condition_logic: st.condition_logic,
    action_type: st.action_type,
    quantity: st.quantity,
    price_type: st.price_type,
    limit_price: st.limit_price,
    conditions: (st.conditions || []).map(c => ({
      field: c.field, operator: c.operator, value: c.value,
    })),
  }))
  showStrategyDialog.value = true
}

const loadStrategies = async () => {
  strategyLoading.value = true
  try {
    const res = await api.getStrategies()
    strategies.value = res.data || []
  } catch (e) {
    ElMessage.error('加载策略失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    strategyLoading.value = false
  }
}

const saveStrategy = async () => {
  if (!strategyForm.name || !strategyForm.stock_code) {
    ElMessage.warning('请填写策略名称和股票代码')
    return
  }
  if (strategyForm.steps.length === 0) {
    ElMessage.warning('请至少添加一个步骤')
    return
  }
  try {
    const payload = {
      name: strategyForm.name,
      stock_code: strategyForm.stock_code,
      stock_name: strategyForm.stock_name,
      market: strategyForm.market,
      mode: strategyForm.mode,
      sim_initial_cash: strategyForm.sim_initial_cash,
      notes: strategyForm.notes || null,
      steps: strategyForm.steps,
    }
    if (editingStrategy.value) {
      await api.updateStrategy(editingStrategy.value.id, { ...payload })
    } else {
      await api.createStrategy(payload)
    }
    ElMessage.success(editingStrategy.value ? '策略已更新' : '策略已创建')
    showStrategyDialog.value = false
    resetStrategyForm()
    await loadStrategies()
  } catch (e) {
    ElMessage.error('保存失败: ' + (e.response?.data?.detail || e.message))
  }
}

const deleteStrategy = async (s) => {
  try {
    await ElMessageBox.confirm(`确定删除策略 "${s.name}"?`, '确认删除', { type: 'warning' })
    await api.deleteStrategy(s.id)
    ElMessage.success('已删除')
    if (strategyDetailId.value === s.id) {
      strategyDetailId.value = null
      strategyDetail.value = null
    }
    await loadStrategies()
  } catch (e) {
    if (e !== 'cancel') {
      ElMessage.error('删除失败: ' + (e.response?.data?.detail || e.message))
    }
  }
}

const activateStrategy = async (s) => {
  try {
    await api.activateStrategy(s.id)
    ElMessage.success('策略已启动')
    await loadStrategies()
    if (strategyDetailId.value === s.id) await loadStrategyDetail(s.id)
  } catch (e) {
    ElMessage.error('启动失败: ' + (e.response?.data?.detail || e.message))
  }
}

const pauseStrategy = async (s) => {
  try {
    await api.pauseStrategy(s.id)
    ElMessage.success('策略已暂停')
    await loadStrategies()
    if (strategyDetailId.value === s.id) await loadStrategyDetail(s.id)
  } catch (e) {
    ElMessage.error('暂停失败: ' + (e.response?.data?.detail || e.message))
  }
}

const cancelStrategy = async (s) => {
  try {
    await ElMessageBox.confirm(`确定取消策略 "${s.name}"?`, '确认取消', { type: 'warning' })
    await api.cancelStrategy(s.id)
    ElMessage.success('策略已取消')
    await loadStrategies()
    if (strategyDetailId.value === s.id) await loadStrategyDetail(s.id)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('取消失败: ' + (e.response?.data?.detail || e.message))
  }
}

const resetStrategy = async (s) => {
  try {
    await ElMessageBox.confirm(`重置将清除所有执行记录和模拟数据, 继续?`, '确认重置', { type: 'warning' })
    await api.resetStrategy(s.id)
    ElMessage.success('策略已重置')
    await loadStrategies()
    if (strategyDetailId.value === s.id) await loadStrategyDetail(s.id)
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('重置失败: ' + (e.response?.data?.detail || e.message))
  }
}

const loadStrategyDetail = async (id) => {
  stopAutoTick()
  liveQuote.value = null
  strategyDetailId.value = id
  try {
    const res = await api.getStrategy(id)
    strategyDetail.value = res.data
  } catch (e) {
    ElMessage.error('加载策略详情失败')
  }
  loadStrategyExecs(id)
}

const loadStrategyExecs = async (id) => {
  strategyExecsLoading.value = true
  try {
    const res = await api.getStrategyExecutions(id)
    strategyExecs.value = res.data || []
  } catch (e) {
    strategyExecs.value = []
  } finally {
    strategyExecsLoading.value = false
  }
}

const doTestTick = async () => {
  if (!strategyDetail.value || testTickPrice.value <= 0) {
    ElMessage.warning('请输入有效的测试价格')
    return
  }
  testTickLoading.value = true
  try {
    const res = await api.testStrategyTick(strategyDetail.value.id, {
      price: testTickPrice.value,
    })
    const evts = res.data.events || []
    strategyDetail.value = res.data.strategy
    await loadStrategyExecs(strategyDetail.value.id)
    await loadStrategies()
    if (evts.length > 0) {
      ElMessage.success(`触发了 ${evts.length} 个事件`)
    } else {
      ElMessage.info('条件未满足, 无事件触发')
    }
  } catch (e) {
    ElMessage.error('测试失败: ' + (e.response?.data?.detail || e.message))
  } finally {
    testTickLoading.value = false
  }
}

const doAutoTick = async () => {
  if (!strategyDetail.value) return
  autoTickLoading.value = true
  try {
    const res = await api.autoTickStrategy(strategyDetail.value.id)
    const data = res.data
    if (data.quote) liveQuote.value = data.quote
    if (data.quote) appendTickToIntraday(data.quote)
    if (data.strategy) strategyDetail.value = data.strategy
    const evts = data.events || []
    if (evts.length > 0) {
      ElMessage.success(`触发了 ${evts.length} 个事件`)
      await loadStrategyExecs(strategyDetail.value.id)
      await loadStrategies()
    }
    if (data.error) {
      ElMessage.warning(data.error)
    }
    // Stop polling if strategy is no longer active
    if (data.strategy && data.strategy.status !== 'active') {
      stopAutoTick()
      await loadStrategies()
    }
  } catch (e) {
    const msg = e.response?.data?.detail || e.message
    if (msg.includes('未激活')) {
      stopAutoTick()
    }
    // Silently ignore other errors during polling
  } finally {
    autoTickLoading.value = false
  }
}

const startAutoTick = () => {
  if (autoTickTimer.value) return
  liveQuote.value = null
  intradayData.value = []
  intradayPrevClose.value = null
  loadIntradayData()   // fetch historical minute data first
  doAutoTick()         // immediate first tick
  autoTickTimer.value = setInterval(doAutoTick, autoTickInterval.value)
}

const stopAutoTick = () => {
  if (autoTickTimer.value) {
    clearInterval(autoTickTimer.value)
    autoTickTimer.value = null
  }
  if (intradayChart) {
    intradayChart.dispose()
    intradayChart = null
  }
}

const onIntervalChange = (val) => {
  // If monitoring is active, restart with new interval
  if (autoTickTimer.value) {
    clearInterval(autoTickTimer.value)
    autoTickTimer.value = setInterval(doAutoTick, val)
  }
}

const loadIntradayData = async () => {
  if (!strategyDetail.value) return
  try {
    const res = await api.getIntradayKlinesByCode(strategyDetail.value.stock_code, { scale: 5, limit: 240 })
    const klines = res.data.klines || []
    if (klines.length > 0) {
      // First point's open can serve as prev_close approximation
      intradayPrevClose.value = klines[0].open
      // Compute cumulative average price
      let cumAmount = 0
      let cumVolume = 0
      intradayData.value = klines.map(k => {
        const timeStr = k.time.includes(' ') ? k.time.split(' ')[1].substring(0, 5) : k.time
        cumAmount += k.close * k.volume
        cumVolume += k.volume
        return {
          time: timeStr,
          close: k.close,
          volume: k.volume,
          avg_price: cumVolume > 0 ? cumAmount / cumVolume : k.close,
        }
      })
    }
    nextTick(() => renderIntradayChart())
  } catch (e) {
    console.error('Failed to load intraday data:', e)
  }
}

const appendTickToIntraday = (quote) => {
  if (!quote || !quote.price) return
  const now = new Date()
  const timeStr = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0')

  // Compute running average
  const arr = intradayData.value
  let cumAmount = 0
  let cumVolume = 0
  for (const p of arr) {
    cumAmount += p.close * p.volume
    cumVolume += p.volume
  }
  cumAmount += quote.price * (quote.volume || 0)
  cumVolume += (quote.volume || 0)

  // Avoid duplicate time entries — update last if same minute
  if (arr.length > 0 && arr[arr.length - 1].time === timeStr) {
    arr[arr.length - 1].close = quote.price
    arr[arr.length - 1].volume = quote.volume || 0
    arr[arr.length - 1].avg_price = cumVolume > 0 ? cumAmount / cumVolume : quote.price
  } else {
    arr.push({
      time: timeStr,
      close: quote.price,
      volume: quote.volume || 0,
      avg_price: cumVolume > 0 ? cumAmount / cumVolume : quote.price,
    })
  }

  if (!intradayPrevClose.value && quote.price) {
    intradayPrevClose.value = quote.price
  }

  nextTick(() => renderIntradayChart())
}

const renderIntradayChart = () => {
  const data = intradayData.value
  if (!intradayChartRef.value || data.length === 0) return

  if (!intradayChart) {
    intradayChart = echarts.init(intradayChartRef.value)
  }

  const times = data.map(d => d.time)
  const prices = data.map(d => d.close)
  const avgPrices = data.map(d => d.avg_price)
  const volumes = data.map(d => d.volume)
  const prevClose = intradayPrevClose.value || prices[0]

  // Color volumes by price direction
  const volColors = data.map((d, i) => {
    if (i === 0) return d.close >= prevClose ? 'rgba(239,83,80,0.6)' : 'rgba(38,166,91,0.6)'
    return d.close >= data[i - 1].close ? 'rgba(239,83,80,0.6)' : 'rgba(38,166,91,0.6)'
  })

  // Price range for y-axis
  const allPrices = [...prices, ...avgPrices, prevClose]
  const minP = Math.min(...allPrices)
  const maxP = Math.max(...allPrices)
  const pad = (maxP - minP) * 0.15 || 0.1
  const yMin = Math.max(0, minP - pad)
  const yMax = maxP + pad

  const option = {
    animation: false,
    backgroundColor: '#1e1e2f',
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: 'rgba(30,30,47,0.95)',
      borderColor: '#444',
      textStyle: { color: '#ddd', fontSize: 12 },
      formatter: (params) => {
        if (!params || params.length === 0) return ''
        const idx = params[0].dataIndex
        const d = data[idx]
        if (!d) return ''
        const pctVal = prevClose > 0 ? ((d.close - prevClose) / prevClose * 100) : 0
        const color = pctVal >= 0 ? '#ef5350' : '#26a65b'
        const pctStr = (pctVal >= 0 ? '+' : '') + pctVal.toFixed(2)
        const vol = d.volume >= 10000 ? (d.volume / 10000).toFixed(1) + '万' : d.volume
        return `<div style="line-height:1.7">
          <div style="font-weight:bold">${d.time}</div>
          <div>价格: <span style="color:${color};font-weight:bold">${d.close.toFixed(2)}</span></div>
          <div>涨跌: <span style="color:${color}">${pctStr}%</span></div>
          <div>均价: <span style="color:#f5c842">${d.avg_price.toFixed(2)}</span></div>
          <div>成交量: ${vol}</div>
        </div>`
      },
    },
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
      label: { backgroundColor: '#333' },
    },
    grid: [
      { left: 60, right: 16, top: 16, height: '58%' },
      { left: 60, right: 16, top: '78%', height: '16%' },
    ],
    xAxis: [
      {
        type: 'category', data: times, gridIndex: 0,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
      {
        type: 'category', data: times, gridIndex: 1,
        axisLine: { lineStyle: { color: '#444' } },
        axisTick: { show: false },
        axisLabel: { color: '#888', fontSize: 10, interval: Math.max(1, Math.floor(times.length / 6)) },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      {
        type: 'value', gridIndex: 0,
        min: yMin.toFixed(2) * 1, max: yMax.toFixed(2) * 1,
        axisLine: { lineStyle: { color: '#444' } },
        axisLabel: { color: '#aaa', fontSize: 10 },
        splitLine: { lineStyle: { color: '#2a2a40', type: 'dashed' } },
      },
      {
        type: 'value', gridIndex: 1,
        axisLine: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '价格',
        type: 'line',
        xAxisIndex: 0, yAxisIndex: 0,
        data: prices,
        symbol: 'none',
        lineStyle: { width: 1.5, color: '#409eff' },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(64,158,255,0.25)' },
            { offset: 1, color: 'rgba(64,158,255,0.02)' },
          ]),
        },
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#888', type: 'dashed', width: 1 },
          data: [{ yAxis: prevClose, label: { formatter: prevClose.toFixed(2), color: '#aaa', fontSize: 10 } }],
        },
      },
      {
        name: '均价',
        type: 'line',
        xAxisIndex: 0, yAxisIndex: 0,
        data: avgPrices,
        symbol: 'none',
        lineStyle: { width: 1, color: '#f5c842', type: 'dashed' },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1, yAxisIndex: 1,
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: volColors[i] },
        })),
      },
    ],
  }

  intradayChart.setOption(option, true)
}

const tradeStatusType = (status) => {
  const map = {
    draft: 'info', active: 'success', paused: 'warning',
    completed: '', cancelled: 'info', error: 'danger',
    waiting: 'info', watching: 'success', triggered: 'warning',
    executing: 'warning', filled: '', failed: 'danger',
  }
  return map[status] || 'info'
}

const tradeStatusLabel = (status) => {
  const map = {
    draft: '草稿', active: '运行中', paused: '已暂停',
    completed: '已完成', cancelled: '已取消', error: '异常',
    waiting: '等待中', watching: '监控中', triggered: '已触发',
    executing: '执行中', filled: '已成交', failed: '失败',
  }
  return map[status] || status
}

const fieldLabel = (field) => {
  const f = CONDITION_FIELDS.find(f => f.value === field)
  return f ? f.label : field
}

const simPnl = (s) => {
  if (!s || s.sim_avg_cost <= 0 || s.sim_holdings <= 0) return null
  // PnL shown as the difference between current cash+holdings vs initial cash
  return s.sim_cash + s.sim_holdings * s.sim_avg_cost - s.sim_initial_cash
}

// --- Cross-Sectional Quantitative Analysis ---
const quantLoading = ref(false)
const quantResult = ref(null)
const quantError = ref('')
const quantEquityRef = ref(null)
const quantRadarRef = ref(null)
let quantEquityChart = null
let quantRadarChart = null

// Async task tracking
const quantTaskId = ref(null)
const quantTaskMsg = ref('')
let quantPollTimer = null

// History & view mode
const quantTab = ref('new')  // 'new' | 'history'
const quantHistory = ref([])
const quantHistoryTotal = ref(0)
const quantHistoryLoading = ref(false)
const quantHistoryPage = ref(1)
const quantViewingRunId = ref(null)
const quantEditingItem = ref(null)
const quantEditForm = reactive({ name: '', notes: '' })
const quantEditDialogVisible = ref(false)

// Iteration tracking
const quantIterations = ref([])
const quantIterationsLoading = ref(false)
const quantIterateDialogVisible = ref(false)
const quantIterateRunId = ref(null)
const quantIterateInfo = ref(null)
const quantIterating = ref(false)
const quantIterViewNum = ref(null) // Selected iteration number to view
const quantResultIterViewNum = ref(null) // Iteration selector in main result view
const quantResultIterations = ref([]) // Iterations for main result view

// Computed: currently viewed iteration item
const quantIterViewItem = computed(() => {
  if (quantIterViewNum.value == null) return null
  return quantIterations.value.find(it => it.iteration_num === quantIterViewNum.value) || null
})

// Computed: buy stock names for current viewed iteration
const quantIterViewBuyNames = computed(() => {
  const item = quantIterViewItem.value
  if (!item || !item.new_buys?.length || !item.portfolio?.length) return ''
  const nameMap = {}
  item.portfolio.forEach(s => { nameMap[s.stock_code] = s.stock_name })
  // Also check previous iteration's portfolio for sell names
  return item.new_buys.map(c => nameMap[c] ? `${c}(${nameMap[c]})` : c).join(', ')
})

// Computed: sell stock names for current viewed iteration (look up from prev iteration's portfolio)
const quantIterViewSellNames = computed(() => {
  const item = quantIterViewItem.value
  if (!item || !item.new_sells?.length) return ''
  // Find previous iteration to get sell stock names
  const prevNum = item.iteration_num - 1
  const prevIter = quantIterations.value.find(it => it.iteration_num === prevNum)
  const nameMap = {}
  if (prevIter && prevIter.portfolio) {
    prevIter.portfolio.forEach(s => { nameMap[s.stock_code] = s.stock_name })
  }
  return item.new_sells.map(c => nameMap[c] ? `${c}(${nameMap[c]})` : c).join(', ')
})

const onQuantIterViewChange = () => { /* reactivity handles the rest via computed */ }

// Computed: portfolio to display in main result view (original or iteration)
const quantResultDisplayPortfolio = computed(() => {
  if (quantResultIterViewNum.value != null && quantResultIterations.value.length > 0) {
    const iter = quantResultIterations.value.find(it => it.iteration_num === quantResultIterViewNum.value)
    if (iter && iter.portfolio?.length > 0) return iter.portfolio
  }
  return quantResult.value?.portfolio?.stocks || []
})

// Computed: selected iteration item in main result view
const quantResultIterViewItem = computed(() => {
  if (quantResultIterViewNum.value == null) return null
  return quantResultIterations.value.find(it => it.iteration_num === quantResultIterViewNum.value) || null
})

// Config controls
const quantTopN = ref(30)
const quantRebalanceFreq = ref(10)
const quantIndustryNeutral = ref(false)

const CATEGORY_COLORS = {
  value: '#409eff',
  momentum: '#e6a23c',
  reversal: '#67c23a',
  volume: '#909399',
  volatility: '#f56c6c',
  technical: '#b37feb',
  moneyflow: '#e040fb',
  stat: '#00bcd4',
}

// Pre-filter controls
const quantPresets = ref([])
const quantIndustries = ref([])
const quantSelectedPreset = ref('')
const quantSelectedIndustries = ref([])
const quantExcludedIndustries = ref([])
const quantMarketCapMin = ref(null)
const quantMarketCapMax = ref(null)
const quantPeMin = ref(null)
const quantPeMax = ref(null)
const quantPbMin = ref(null)
const quantPbMax = ref(null)
const quantShowAdvancedFilters = ref(false)

// Backtest date range
const quantBacktestPreset = ref('default')  // 'default', '1y', '2y', '3y', '5y', 'all', 'custom'
const quantBacktestStart = ref('')
const quantBacktestEnd = ref('')

const BACKTEST_RANGE_PRESETS = [
  { value: 'default', label: '默认(近2年)', desc: '最近约500个交易日' },
  { value: '1y', label: '近1年', desc: '最近约250个交易日' },
  { value: '3y', label: '近3年', desc: '最近约750个交易日' },
  { value: '5y', label: '近5年', desc: '最近约1250个交易日' },
  { value: 'all', label: '全部数据', desc: '使用全部可用K线 (~10年)' },
  { value: 'custom', label: '自定义', desc: '手动指定起止日期' },
]

const onBacktestPresetChange = (val) => {
  if (val === 'default') {
    quantBacktestStart.value = ''
    quantBacktestEnd.value = ''
    return
  }
  if (val !== 'custom') {
    quantBacktestEnd.value = ''
  }
  if (val && val !== 'custom' && val !== 'all' && val !== 'default') {
    const now = new Date()
    const yearsMap = { '1y': 1, '3y': 3, '5y': 5 }
    const years = yearsMap[val] || 2
    const start = new Date(now.getFullYear() - years, now.getMonth(), now.getDate())
    quantBacktestStart.value = start.toISOString().substring(0, 10)
  } else if (val === 'all') {
    quantBacktestStart.value = '2016-01-01'
  }
}

const loadQuantPresets = async () => {
  try {
    const [presetsRes, industriesRes] = await Promise.all([
      api.getQuantPresets(),
      api.getQuantIndustries(),
    ])
    quantPresets.value = presetsRes.data.presets || []
    quantIndustries.value = (industriesRes.data.industries || []).map(i => i.name)
  } catch (e) {
    // silent
  }
}

// Quant industry +/- mode helpers
const quantIndustryTemp = ref('')
const addQuantIndustry = (val) => {
  if (!val) return
  if (!quantSelectedIndustries.value.includes(val) && !quantExcludedIndustries.value.includes(val)) {
    quantSelectedIndustries.value.push(val)
  }
  quantIndustryTemp.value = ''
}
const toggleQuantIndustry = (ind) => {
  if (quantSelectedIndustries.value.includes(ind)) {
    quantSelectedIndustries.value = quantSelectedIndustries.value.filter(i => i !== ind)
    quantExcludedIndustries.value.push(ind)
  } else if (quantExcludedIndustries.value.includes(ind)) {
    quantExcludedIndustries.value = quantExcludedIndustries.value.filter(i => i !== ind)
    quantSelectedIndustries.value.push(ind)
  }
}
const removeQuantIndustry = (ind) => {
  quantSelectedIndustries.value = quantSelectedIndustries.value.filter(i => i !== ind)
  quantExcludedIndustries.value = quantExcludedIndustries.value.filter(i => i !== ind)
}
const quantIndustryAvailable = computed(() => {
  const selected = new Set([...quantSelectedIndustries.value, ...quantExcludedIndustries.value])
  return quantIndustries.value.filter(i => !selected.has(i))
})

const onPresetChange = (val) => {
  if (val && val !== 'custom') {
    // Populate custom filter fields from preset filters so user can see & modify
    const preset = quantPresets.value.find(p => p.name === val)
    const f = preset?.filters || {}
    quantSelectedIndustries.value = f.industries || []
    quantExcludedIndustries.value = []
    quantMarketCapMin.value = f.market_cap_min ?? null
    quantMarketCapMax.value = f.market_cap_max ?? null
    quantPeMin.value = f.pe_min ?? null
    quantPeMax.value = f.pe_max ?? null
    quantPbMin.value = f.pb_min ?? null
    quantPbMax.value = f.pb_max ?? null
    // Auto-expand advanced filters so user can see what's applied
    if (Object.keys(f).length > 0) {
      quantShowAdvancedFilters.value = true
    }
  } else if (!val) {
    // Preset cleared — reset all filter fields
    quantSelectedIndustries.value = []
    quantExcludedIndustries.value = []
    quantMarketCapMin.value = null
    quantMarketCapMax.value = null
    quantPeMin.value = null
    quantPeMax.value = null
    quantPbMin.value = null
    quantPbMax.value = null
  }
}

const stopQuantPoll = () => {
  if (quantPollTimer) {
    clearInterval(quantPollTimer)
    quantPollTimer = null
  }
}

const pollQuantTask = async () => {
  if (!quantTaskId.value) return
  try {
    const res = await api.getQuantTaskStatus(quantTaskId.value)
    const data = res.data
    quantTaskMsg.value = data.message || ''
    if (data.status === 'completed') {
      stopQuantPoll()
      quantLoading.value = false
      quantResult.value = data.result
      quantViewingRunId.value = data.run_id || null
      quantTaskId.value = null
      ElMessage.success('分析完成')
      await nextTick()
      renderQuantEquityChart()
      renderQuantRadar()
    } else if (data.status === 'error') {
      stopQuantPoll()
      quantLoading.value = false
      quantError.value = data.message || '分析失败'
      quantTaskId.value = null
      ElMessage.error(data.message || '分析失败')
    }
  } catch (e) {
    // Polling error - keep trying
  }
}

const runQuantAnalysis = async () => {
  quantLoading.value = true
  quantError.value = ''
  quantResult.value = null
  quantViewingRunId.value = null
  quantTaskMsg.value = ''
  stopQuantPoll()
  try {
    const params = {
      top_n: quantTopN.value,
      rebalance_freq: quantRebalanceFreq.value,
      industry_neutral: quantIndustryNeutral.value,
    }
    // Apply preset or manual filters
    if (quantSelectedPreset.value && quantSelectedPreset.value !== 'custom') {
      params.preset = quantSelectedPreset.value
    }
    if (quantSelectedIndustries.value.length > 0) {
      params.industries = quantSelectedIndustries.value.join(',')
    }
    if (quantExcludedIndustries.value.length > 0) {
      params.exclude_industries = quantExcludedIndustries.value.join(',')
    }
    if (quantMarketCapMin.value != null) params.market_cap_min = quantMarketCapMin.value
    if (quantMarketCapMax.value != null) params.market_cap_max = quantMarketCapMax.value
    if (quantPeMin.value != null) params.pe_min = quantPeMin.value
    if (quantPeMax.value != null) params.pe_max = quantPeMax.value
    if (quantPbMin.value != null) params.pb_min = quantPbMin.value
    if (quantPbMax.value != null) params.pb_max = quantPbMax.value
    // Backtest date range
    if (quantBacktestStart.value) params.backtest_start = quantBacktestStart.value
    if (quantBacktestEnd.value) params.backtest_end = quantBacktestEnd.value
    const res = await api.runQuantAnalysis(params)
    const data = res.data
    quantTaskId.value = data.task_id
    quantTaskMsg.value = data.message || '分析任务已提交...'
    // Start polling every 2 seconds
    quantPollTimer = setInterval(pollQuantTask, 2000)
  } catch (e) {
    const msg = e.response?.data?.detail || e.message || '分析失败'
    quantError.value = msg
    quantLoading.value = false
    ElMessage.error(msg)
  }
}

// History functions
const loadQuantHistory = async () => {
  quantHistoryLoading.value = true
  try {
    const res = await api.getQuantHistory(20, (quantHistoryPage.value - 1) * 20)
    quantHistory.value = res.data.items || []
    quantHistoryTotal.value = res.data.total || 0
  } catch (e) {
    ElMessage.error('加载历史记录失败')
  } finally {
    quantHistoryLoading.value = false
  }
}

const viewQuantResult = async (runId) => {
  quantLoading.value = true
  quantError.value = ''
  quantResult.value = null
  quantResultIterations.value = []
  quantResultIterViewNum.value = null
  // Remember where we came from for back-button support
  const cameFromHistory = quantTab.value === 'history'
  quantTab.value = 'new'
  if (cameFromHistory) {
    pushMenuState('quant', { quantTab: 'new', quantRunId: runId })
  }
  try {
    const res = await api.getQuantResult(runId)
    quantResult.value = res.data
    quantViewingRunId.value = runId
    quantLoading.value = false
    await nextTick()
    renderQuantEquityChart()
    renderQuantRadar()
    // If this result has iterations, load them for the portfolio switcher
    if (res.data.auto_iterate && res.data.total_iterations > 0) {
      try {
        const iterRes = await api.getQuantIterations(runId)
        quantResultIterations.value = iterRes.data.iterations || []
        // Auto-select: prefer active iteration, then fallback to 第0期
        const iters = iterRes.data.iterations || []
        const active = iters.find(it => it.status === 'active')
        if (active) {
          quantResultIterViewNum.value = active.iteration_num
        } else if (iters.length > 0) {
          quantResultIterViewNum.value = iters[iters.length - 1].iteration_num  // last (第0期 is last in desc order)
        }
      } catch (e) { /* non-critical, ignore */ }
    }
  } catch (e) {
    quantLoading.value = false
    const msg = e.response?.data?.detail || e.message || '加载失败'
    quantError.value = msg
    ElMessage.error(msg)
  }
}

const openQuantEditDialog = (item) => {
  quantEditingItem.value = item
  quantEditForm.name = item.name || ''
  quantEditForm.notes = item.notes || ''
  quantEditDialogVisible.value = true
}

const saveQuantEdit = async () => {
  if (!quantEditingItem.value) return
  try {
    await api.updateQuantResult(quantEditingItem.value.run_id, {
      name: quantEditForm.name,
      notes: quantEditForm.notes,
    })
    ElMessage.success('已更新')
    quantEditDialogVisible.value = false
    loadQuantHistory()
  } catch (e) {
    ElMessage.error('更新失败')
  }
}

const deleteQuantResult = async (runId) => {
  try {
    await ElMessageBox.confirm('确定删除该分析记录？', '确认删除', { type: 'warning' })
    await api.deleteQuantResult(runId)
    ElMessage.success('已删除')
    loadQuantHistory()
    if (quantViewingRunId.value === runId) {
      quantResult.value = null
      quantViewingRunId.value = null
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

// --- Iteration management ---
const toggleQuantIterate = async (row) => {
  const newState = !row.auto_iterate
  const actionText = newState ? '启用' : '停用'
  try {
    await ElMessageBox.confirm(`确定${actionText}自动迭代？${newState ? '系统将按调仓周期自动重新选股。' : ''}`, `${actionText}自动迭代`, { type: 'warning' })
    const res = await api.toggleQuantIterate(row.run_id, newState)
    // If historical simulation was launched, poll for completion
    if (res.data?.task_id && res.data?.historical) {
      ElMessage.info(res.data.message || '历史迭代任务已提交')
      const taskId = res.data.task_id
      const pollInterval = setInterval(async () => {
        try {
          const statusRes = await api.getQuantTaskStatus(taskId)
          const st = statusRes.data
          if (st.status === 'completed') {
            clearInterval(pollInterval)
            ElMessage.success(st.message || '历史迭代完成')
            loadQuantHistory()
          } else if (st.status === 'error') {
            clearInterval(pollInterval)
            ElMessage.error(st.message || '历史迭代失败')
            loadQuantHistory()
          }
          // Update message in history while running
          row.iterate_status = st.status === 'running' ? 'running' : row.iterate_status
        } catch (e) { /* ignore poll errors */ }
      }, 5000)
    } else {
      ElMessage.success(`已${actionText}自动迭代`)
      loadQuantHistory()
    }
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(`${actionText}失败: ${e?.response?.data?.detail || e.message}`)
  }
}

const openQuantIterateDialog = async (row) => {
  quantIterateRunId.value = row.run_id
  quantIterateDialogVisible.value = true
  quantIterateInfo.value = row
  await loadQuantIterations(row.run_id)
}

const loadQuantIterations = async (runId) => {
  quantIterationsLoading.value = true
  try {
    const res = await api.getQuantIterations(runId)
    quantIterations.value = res.data.iterations || []
    quantIterateInfo.value = {
      ...quantIterateInfo.value,
      auto_iterate: res.data.auto_iterate,
      iterate_status: res.data.iterate_status,
      next_iterate_date: res.data.next_iterate_date,
      total_iterations: res.data.total_iterations,
      live_nav: res.data.live_nav,
      live_return_pct: res.data.live_return_pct,
    }
    // Auto-select the latest iteration (active one first, otherwise highest num)
    const iters = res.data.iterations || []
    if (iters.length > 0) {
      const active = iters.find(it => it.status === 'active')
      quantIterViewNum.value = active ? active.iteration_num : iters[0].iteration_num
    } else {
      quantIterViewNum.value = null
    }
  } catch (e) {
    ElMessage.error('加载迭代记录失败')
  } finally {
    quantIterationsLoading.value = false
  }
}

const triggerQuantIteration = async () => {
  if (!quantIterateRunId.value) return
  quantIterating.value = true
  try {
    const res = await api.triggerQuantIteration(quantIterateRunId.value)
    ElMessage.success(res.data.message || '迭代任务已提交')
    // Poll task status
    if (res.data.task_id) {
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        try {
          const statusRes = await api.getQuantTaskStatus(res.data.task_id)
          if (statusRes.data.status === 'completed') {
            clearInterval(poll)
            quantIterating.value = false
            ElMessage.success('迭代完成')
            await loadQuantIterations(quantIterateRunId.value)
            loadQuantHistory()
          } else if (statusRes.data.status === 'error') {
            clearInterval(poll)
            quantIterating.value = false
            ElMessage.error('迭代失败: ' + (statusRes.data.message || ''))
          } else if (attempts > 300) {
            clearInterval(poll)
            quantIterating.value = false
            ElMessage.warning('迭代超时，请稍后刷新查看')
          }
        } catch {
          clearInterval(poll)
          quantIterating.value = false
        }
      }, 2000)
    } else {
      // Fallback: just reload after a delay
      setTimeout(async () => {
        quantIterating.value = false
        await loadQuantIterations(quantIterateRunId.value)
        loadQuantHistory()
      }, 5000)
    }
  } catch (e) {
    quantIterating.value = false
    ElMessage.error('触发迭代失败: ' + (e?.response?.data?.detail || e.message))
  }
}

const quantValidFactors = computed(() => {
  if (!quantResult.value?.factors) return []
  return quantResult.value.factors.filter(f => f.is_valid).sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight))
})

const quantInvalidFactors = computed(() => {
  if (!quantResult.value?.factors) return []
  return quantResult.value.factors.filter(f => !f.is_valid)
})

const quantCategoryScores = computed(() => {
  if (!quantResult.value?.factors) return []
  const cats = {}
  for (const f of quantResult.value.factors) {
    if (!cats[f.category]) {
      cats[f.category] = { category: f.category, label: f.category_label, factors: [], validCount: 0, totalWeight: 0, avgIcIr: 0 }
    }
    cats[f.category].factors.push(f)
    if (f.is_valid) cats[f.category].validCount++
    cats[f.category].totalWeight += Math.abs(f.weight)
    cats[f.category].avgIcIr += Math.abs(f.ic_ir)
  }
  return Object.values(cats).map(c => ({
    ...c,
    avgIcIr: c.factors.length > 0 ? (c.avgIcIr / c.factors.length) : 0,
    score: c.factors.length > 0 ? Math.round(c.totalWeight * 100) : 0,
  }))
})

const renderQuantEquityChart = () => {
  if (!quantEquityRef.value || !quantResult.value?.backtest?.equity_curve) return
  if (quantEquityChart) quantEquityChart.dispose()
  quantEquityChart = echarts.init(quantEquityRef.value)

  const ec = quantResult.value.backtest.equity_curve
  const dates = ec.map(e => e.date)
  const navs = ec.map(e => (e.nav * 100).toFixed(1))

  quantEquityChart.setOption({
    tooltip: { trigger: 'axis', formatter: p => `${p[0].axisValue}<br/>${p[0].marker} 净值: ${p[0].value}%` },
    grid: { left: 55, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10, interval: Math.floor(dates.length / 6) } },
    yAxis: { type: 'value', name: 'NAV %', axisLabel: { fontSize: 10 } },
    series: [{
      type: 'line',
      data: navs,
      lineStyle: { width: 2, color: '#409eff' },
      areaStyle: { color: 'rgba(64,158,255,0.1)' },
      itemStyle: { color: '#409eff' },
      symbol: 'none',
    }],
    dataZoom: [{ type: 'inside', start: 0, end: 100 }],
  })
}

const renderQuantRadar = () => {
  if (!quantRadarRef.value || !quantResult.value) return
  if (quantRadarChart) quantRadarChart.dispose()
  quantRadarChart = echarts.init(quantRadarRef.value)

  const cats = quantCategoryScores.value
  if (cats.length === 0) return

  const indicators = cats.map(c => ({ name: c.label, max: 100 }))
  const maxW = Math.max(...cats.map(x => x.totalWeight * 100 || 1))
  const values = cats.map(c => Math.round(c.totalWeight * 100 * (100 / maxW)))

  quantRadarChart.setOption({
    tooltip: {},
    radar: {
      indicator: indicators,
      shape: 'polygon',
      splitArea: { areaStyle: { color: ['rgba(64,158,255,0.05)', 'rgba(64,158,255,0.1)'] } },
      axisName: { color: '#333', fontSize: 12 },
    },
    series: [{
      type: 'radar',
      data: [{
        value: values,
        name: '因子权重分布',
        areaStyle: { color: 'rgba(64,158,255,0.25)' },
        lineStyle: { color: '#409eff', width: 2 },
        itemStyle: { color: '#409eff' },
      }],
    }],
  })
}

// --- Init ---
onMounted(async () => {
  // Set initial history state so back button can return here
  history.replaceState({ menu: 'stocks' }, '', '#stocks')
  window.addEventListener('popstate', handlePopState)

  // Load display timezone first (works without auth)
  await loadDisplayTimezone()

  // Check for existing auth token
  const token = localStorage.getItem('astock_token')
  if (token) {
    await fetchCurrentUser()
    if (currentUser.value) {
      isLoggedIn.value = true
      loadFavorites()
      loadEnrichedFavorites()
      loadEnrichedStocks()
    }
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('popstate', handlePopState)
  if (aiPollTimer) { clearInterval(aiPollTimer); aiPollTimer = null }
  stopAutoTick()
  stopQuantPoll()
  if (quantRadarChart) { quantRadarChart.dispose(); quantRadarChart = null }
  if (quantEquityChart) { quantEquityChart.dispose(); quantEquityChart = null }
})
</script>

<template>
  <!-- Login Page -->
  <div v-if="!isLoggedIn" class="login-page">
    <div class="login-card">
      <div class="login-header">
        <el-icon :size="40" color="#409eff"><TrendCharts /></el-icon>
        <h1>AStock 管理平台</h1>
      </div>
      <el-form @submit.prevent="doLogin" class="login-form">
        <el-form-item>
          <el-input v-model="loginForm.username" placeholder="用户名" :prefix-icon="User" size="large" />
        </el-form-item>
        <el-form-item>
          <el-input v-model="loginForm.password" placeholder="密码" type="password" :prefix-icon="Lock" size="large" show-password @keyup.enter="doLogin" />
        </el-form-item>
        <div v-if="loginError" class="login-error">{{ loginError }}</div>
        <el-button type="primary" :loading="loginLoading" size="large" style="width: 100%" @click="doLogin">登 录</el-button>
      </el-form>
      <div class="login-footer">AStock &copy; 2026</div>
    </div>
  </div>

  <!-- Main App -->
  <el-container v-else class="app-container">
    <!-- Header -->
    <el-header class="app-header">
      <div class="header-content">
        <el-icon :size="28" color="#fff" style="margin-right: 10px"><TrendCharts /></el-icon>
        <h1 class="header-title">AStock 管理平台</h1>
      </div>
      <div class="header-right">
        <el-dropdown trigger="click">
          <span class="header-user">
            <el-icon><User /></el-icon>
            <span style="margin-left: 6px;">{{ currentUser?.display_name || currentUser?.username }}</span>
            <el-icon style="margin-left: 4px;"><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item @click="showChangePassword = true">
                <el-icon><Lock /></el-icon> 修改密码
              </el-dropdown-item>
              <el-dropdown-item divided @click="doLogout">
                <el-icon><SwitchButton /></el-icon> 退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </div>
    </el-header>

    <el-container class="main-container">
      <!-- Sidebar -->
      <el-aside width="200px" class="app-sidebar">
        <el-menu
          :default-active="activeMenu"
          class="sidebar-menu"
          background-color="#304156"
          text-color="#bfcbd9"
          active-text-color="#409eff"
          @select="handleMenuSelect"
        >
          <el-menu-item index="stocks" v-if="hasPermission('stocks')">
            <el-icon><Collection /></el-icon>
            <span>自选股管理</span>
          </el-menu-item>
          <el-menu-item index="quotes" v-if="hasPermission('quotes')">
            <el-icon><DataLine /></el-icon>
            <span>行情数据</span>
          </el-menu-item>
          <el-menu-item index="strategy" v-if="hasPermission('strategy')">
            <el-icon><TrendCharts /></el-icon>
            <span>策略交易</span>
          </el-menu-item>
          <el-menu-item index="screener" v-if="hasPermission('screener')">
            <el-icon><Filter /></el-icon>
            <span>智能选股</span>
          </el-menu-item>
          <el-menu-item index="quant" v-if="hasPermission('quant')">
            <el-icon><Histogram /></el-icon>
            <span>量化分析</span>
          </el-menu-item>
          <el-menu-item index="logs" v-if="hasPermission('stocks')">
            <el-icon><Document /></el-icon>
            <span>抓取日志</span>
          </el-menu-item>
          <el-menu-item index="schedule" v-if="hasPermission('schedule')">
            <el-icon><Timer /></el-icon>
            <span>定时更新</span>
          </el-menu-item>
          <el-menu-item index="config" v-if="hasPermission('config')">
            <el-icon><SetUp /></el-icon>
            <span>配置管理</span>
          </el-menu-item>
          <el-menu-item index="users" v-if="isAdmin">
            <el-icon><User /></el-icon>
            <span>用户管理</span>
          </el-menu-item>
        </el-menu>
      </el-aside>

      <!-- Main Content -->
      <el-main class="app-main">

        <!-- ==================== Section 1: Stock Management ==================== -->
        <div v-if="activeMenu === 'stocks'">
          <div class="section-header">
            <h2>自选股管理</h2>
          </div>

          <!-- Search & Add -->
          <el-card class="section-card" shadow="hover">
            <template #header>
              <span class="card-title">搜索添加股票</span>
            </template>
            <el-row :gutter="16" align="middle">
              <el-col :span="8">
                <el-select
                  v-model="searchKeyword"
                  filterable
                  remote
                  reserve-keyword
                  placeholder="输入代码或名称搜索..."
                  :remote-method="handleSearch"
                  :loading="searchLoading"
                  value-key="code"
                  clearable
                  style="width: 100%"
                  @change="selectSearchResult"
                >
                  <el-option
                    v-for="item in searchResults"
                    :key="item.code"
                    :label="`${item.code} - ${item.name}`"
                    :value="item"
                  />
                </el-select>
              </el-col>
              <el-col :span="4">
                <el-input v-model="stockForm.code" placeholder="股票代码" clearable />
              </el-col>
              <el-col :span="4">
                <el-input v-model="stockForm.name" placeholder="股票名称" clearable />
              </el-col>
              <el-col :span="4">
                <el-select v-model="stockForm.market" placeholder="市场" style="width: 100%">
                  <el-option label="上海 (SH)" value="sh" />
                  <el-option label="深圳 (SZ)" value="sz" />
                  <el-option label="北京 (BJ)" value="bj" />
                </el-select>
              </el-col>
              <el-col :span="4">
                <el-button type="primary" @click="addStock" :icon="Plus">
                  添加
                </el-button>
              </el-col>
            </el-row>
          </el-card>

          <!-- Favorites (自选股) -->
          <el-card class="section-card" shadow="hover">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">
                  <el-icon style="color: #f7ba2a; vertical-align: middle; margin-right: 4px"><StarFilled /></el-icon>
                  我的自选股
                  <el-tag size="small" type="warning" style="margin-left: 8px">{{ favorites.length }}</el-tag>
                </span>
                <el-button
                  type="success"
                  :loading="fetchAllLoading"
                  @click="fetchAll"
                  :icon="fetchAllLoading ? null : Refresh"
                >
                  {{ fetchAllLoading ? `抓取中 ${fetchAllProgress.current}/${fetchAllProgress.total} ${fetchAllProgress.currentStock}` : '全部抓取' }}
                </el-button>
              </div>
            </template>
            <el-table
              :data="enrichedFavorites"
              stripe
              border
              style="width: 100%"
              empty-text="暂无自选股，请在下方全部股票列表中点击星标添加"
              size="small"
            >
              <el-table-column width="50" align="center">
                <template #default="{ row }">
                  <el-icon
                    class="fav-star active"
                    @click="toggleFavorite(row)"
                  ><StarFilled /></el-icon>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" width="90">
                <template #default="{ row }">
                  <a class="stock-link" style="font-family: monospace;" @click="navigateToStock(row)">{{ row.code }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" width="100">
                <template #default="{ row }">
                  <a class="stock-link" @click="navigateToStock(row)">{{ row.name }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="market" label="市场" width="70">
                <template #default="{ row }">
                  <el-tag :type="marketTagType(row.market)" size="small">
                    {{ marketLabel(row.market) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="industry" label="行业" width="100">
                <template #default="{ row }">
                  {{ row.industry || '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="total_market_cap" label="总市值" width="100">
                <template #default="{ row }">
                  {{ formatMcap(row.total_market_cap) }}
                </template>
              </el-table-column>
              <el-table-column prop="pe_ttm" label="PE(TTM)" width="90">
                <template #default="{ row }">
                  {{ row.pe_ttm ? row.pe_ttm.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="pb" label="PB" width="70">
                <template #default="{ row }">
                  {{ row.pb ? row.pb.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="latest_kline_date" label="最新K线" width="110">
                <template #default="{ row }">
                  {{ row.latest_kline_date || '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="is_active" label="状态" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                    {{ row.is_active ? '启用' : '停用' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" min-width="220">
                <template #default="{ row }">
                  <el-button size="small" type="primary" @click="fetchStock(row)" :icon="Download" :loading="row._fetching">
                    抓取
                  </el-button>
                  <el-button
                    size="small"
                    :type="row.is_active ? 'warning' : 'success'"
                    @click="toggleActive(row)"
                  >
                    {{ row.is_active ? '停用' : '启用' }}
                  </el-button>
                  <el-button size="small" type="danger" @click="deleteStock(row)" :icon="Delete">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>

          <!-- All Stocks -->
          <el-card class="section-card" shadow="hover">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">
                  全部股票
                  <el-tag size="small" style="margin-left: 8px">{{ enrichedTotal }}</el-tag>
                </span>
                <el-input
                  v-model="stockFilterText"
                  placeholder="筛选代码或名称..."
                  clearable
                  style="width: 240px"
                  size="small"
                />
              </div>
            </template>
            <el-table
              :data="enrichedStocks"
              v-loading="stocksLoading"
              stripe
              border
              style="width: 100%"
              empty-text="暂无股票数据"
              size="small"
            >
              <el-table-column width="50" align="center">
                <template #default="{ row }">
                  <el-icon
                    :class="['fav-star', row.is_favorite ? 'active' : '']"
                    @click="toggleFavorite(row)"
                  >
                    <StarFilled v-if="row.is_favorite" />
                    <Star v-else />
                  </el-icon>
                </template>
              </el-table-column>
              <el-table-column prop="code" label="代码" width="90">
                <template #default="{ row }">
                  <a class="stock-link" style="font-family: monospace;" @click="navigateToStock(row)">{{ row.code }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" width="100">
                <template #default="{ row }">
                  <a class="stock-link" @click="navigateToStock(row)">{{ row.name }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="market" label="市场" width="70">
                <template #default="{ row }">
                  <el-tag :type="marketTagType(row.market)" size="small">
                    {{ marketLabel(row.market) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="industry" label="行业" width="100">
                <template #default="{ row }">
                  {{ row.industry || '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="total_market_cap" label="总市值" width="100">
                <template #default="{ row }">
                  {{ formatMcap(row.total_market_cap) }}
                </template>
              </el-table-column>
              <el-table-column prop="pe_ttm" label="PE(TTM)" width="90">
                <template #default="{ row }">
                  {{ row.pe_ttm ? row.pe_ttm.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="pb" label="PB" width="70">
                <template #default="{ row }">
                  {{ row.pb ? row.pb.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="latest_kline_date" label="最新K线" width="110">
                <template #default="{ row }">
                  {{ row.latest_kline_date || '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="is_active" label="状态" width="70">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                    {{ row.is_active ? '启用' : '停用' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column label="操作" min-width="220">
                <template #default="{ row }">
                  <el-button size="small" type="primary" @click="fetchStock(row)" :icon="Download" :loading="row._fetching">
                    抓取
                  </el-button>
                  <el-button
                    size="small"
                    :type="row.is_active ? 'warning' : 'success'"
                    @click="toggleActive(row)"
                  >
                    {{ row.is_active ? '停用' : '启用' }}
                  </el-button>
                  <el-button size="small" type="danger" @click="deleteStock(row)" :icon="Delete">
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
            <div style="margin-top: 12px; display: flex; justify-content: flex-end;">
              <el-pagination
                v-model:current-page="enrichedPage"
                v-model:page-size="enrichedPageSize"
                :page-sizes="[20, 50, 100, 200]"
                :total="enrichedTotal"
                layout="total, sizes, prev, pager, next, jumper"
                @current-change="handleEnrichedPageChange"
                @size-change="handleEnrichedSizeChange"
              />
            </div>
          </el-card>
        </div>

        <!-- ==================== Section 2: Market Data ==================== -->
        <div v-if="activeMenu === 'quotes'">
          <div class="section-header">
            <h2>行情数据</h2>
          </div>

          <!-- Stock Selector -->
          <el-card class="section-card" shadow="hover">
            <el-row :gutter="16" align="middle">
              <el-col :span="8">
                <el-select
                  v-model="selectedStockId"
                  placeholder="输入代码或名称搜索..."
                  clearable
                  filterable
                  remote
                  :remote-method="handleQuoteStockSearch"
                  :loading="quoteStockLoading"
                  style="width: 100%"
                  @change="onStockSelect"
                  @focus="loadQuoteStockDefaults"
                >
                  <el-option
                    v-for="s in quoteStockOptions"
                    :key="s.id"
                    :label="`${s.code} - ${s.name}`"
                    :value="s.id"
                  />
                </el-select>
              </el-col>
            </el-row>
          </el-card>

          <!-- Stock Profile Info Bar -->
          <el-card class="section-card profile-bar" shadow="hover" v-if="stockProfile">
            <el-row :gutter="16">
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">行业</span>
                  <span class="profile-value">{{ stockProfile.industry || '-' }}</span>
                </div>
              </el-col>
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">PE(TTM)</span>
                  <span class="profile-value">{{ stockProfile.pe_ttm ? stockProfile.pe_ttm.toFixed(2) : '-' }}</span>
                </div>
              </el-col>
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">PB</span>
                  <span class="profile-value">{{ stockProfile.pb ? stockProfile.pb.toFixed(2) : '-' }}</span>
                </div>
              </el-col>
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">总市值</span>
                  <span class="profile-value">{{ stockProfile.total_market_cap ? stockProfile.total_market_cap.toFixed(1) + '亿' : '-' }}</span>
                </div>
              </el-col>
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">流通市值</span>
                  <span class="profile-value">{{ stockProfile.circ_market_cap ? stockProfile.circ_market_cap.toFixed(1) + '亿' : '-' }}</span>
                </div>
              </el-col>
              <el-col :span="4">
                <div class="profile-item">
                  <span class="profile-label">板块</span>
                  <span class="profile-value">{{ stockProfile.sector || '-' }}</span>
                </div>
              </el-col>
            </el-row>
          </el-card>

          <!-- Tabs -->
          <el-card class="section-card" shadow="hover" v-if="selectedStockId">
            <el-tabs v-model="quoteTab" @tab-change="onTabChange">
              <!-- Realtime Quote Tab -->
              <el-tab-pane label="实时行情" name="realtime">
                <!-- Price Panel (loading only here, not affecting chart) -->
                <div v-loading="realtimeLoading" class="realtime-quote-area">
                  <div v-if="realtimeQuote && realtimeQuote.price" class="realtime-panel">
                    <div class="realtime-header">
                      <div class="realtime-price-block">
                        <span class="realtime-price" :class="changePctClass(realtimeQuote.change_pct)">
                          {{ formatPrice(realtimeQuote.price) }}
                        </span>
                        <span class="realtime-change" :class="changePctClass(realtimeQuote.change_pct)">
                          {{ realtimeQuote.change_pct >= 0 ? '+' : '' }}{{ ((realtimeQuote.price || 0) - (realtimeQuote.prev_close || 0)).toFixed(2) }}
                          ({{ formatPct(realtimeQuote.change_pct) }})
                        </span>
                      </div>
                      <div class="realtime-meta">
                        <span class="realtime-source">{{ realtimeQuote.source }}</span>
                        <el-tag v-if="!isTradingTime" size="small" type="info" effect="plain" style="margin-left:4px">已收盘</el-tag>
                        <el-tag v-else size="small" type="success" effect="plain" style="margin-left:4px">交易中</el-tag>
                        <span class="realtime-time">{{ realtimeQuote.timestamp ? formatTz(realtimeQuote.timestamp, { timeOnly: true }) : '' }}</span>
                      </div>
                    </div>
                    <el-row :gutter="12" class="realtime-grid">
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">开盘</span>
                          <span class="realtime-val" :class="changePctClass((realtimeQuote.open || 0) - (realtimeQuote.prev_close || 0))">{{ formatPrice(realtimeQuote.open) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">昨收</span>
                          <span class="realtime-val">{{ formatPrice(realtimeQuote.prev_close) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">最高</span>
                          <span class="realtime-val price-up">{{ formatPrice(realtimeQuote.high) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">最低</span>
                          <span class="realtime-val price-down">{{ formatPrice(realtimeQuote.low) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">成交量</span>
                          <span class="realtime-val">{{ formatVolume(realtimeQuote.volume) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">成交额</span>
                          <span class="realtime-val">{{ formatAmount(realtimeQuote.amount) }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">换手率</span>
                          <span class="realtime-val">{{ realtimeQuote.turnover_rate ? realtimeQuote.turnover_rate.toFixed(2) + '%' : '-' }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="realtime-item">
                          <span class="realtime-label">振幅</span>
                          <span class="realtime-val">{{ realtimeQuote.prev_close ? (((realtimeQuote.high - realtimeQuote.low) / realtimeQuote.prev_close) * 100).toFixed(2) + '%' : '-' }}</span>
                        </div>
                      </el-col>
                    </el-row>
                  </div>
                  <el-alert
                    v-else-if="realtimeQuote && realtimeQuote.error"
                    :title="realtimeQuote.error"
                    type="warning"
                    :closable="false"
                    show-icon
                  />
                </div>

                <!-- Intraday Chart (independent from quote loading, no extra spinner) -->
                <div v-if="selectedStockId && quotesIntradayData.length > 0" class="quotes-intraday-section">
                  <div ref="quotesIntradayChartRef" style="width: 100%; height: 360px;"></div>
                </div>
              </el-tab-pane>

              <!-- Snapshot Tab (historical) -->
              <el-tab-pane label="历史快照" name="snapshot">
                <el-table
                  :data="snapshots"
                  v-loading="snapshotsLoading"
                  stripe
                  border
                  style="width: 100%"
                  empty-text="暂无快照数据"
                  max-height="560"
                >
                  <el-table-column prop="price" label="当前价" width="100">
                    <template #default="{ row }">
                      {{ formatPrice(row.price) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="open" label="开盘价" width="100">
                    <template #default="{ row }">
                      {{ formatPrice(row.open) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="high" label="最高价" width="100">
                    <template #default="{ row }">
                      {{ formatPrice(row.high) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="low" label="最低价" width="100">
                    <template #default="{ row }">
                      {{ formatPrice(row.low) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="volume" label="成交量" width="120">
                    <template #default="{ row }">
                      {{ formatVolume(row.volume) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="amount" label="成交额" width="140">
                    <template #default="{ row }">
                      {{ formatAmount(row.amount) }}
                    </template>
                  </el-table-column>
                  <el-table-column prop="change_pct" label="涨跌幅" width="110">
                    <template #default="{ row }">
                      <span :class="changePctClass(row.change_pct)">
                        {{ formatPct(row.change_pct) }}
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="timestamp" label="时间" min-width="180">
                    <template #default="{ row }">
                      {{ row.timestamp ? formatTz(row.timestamp) : '-' }}
                    </template>
                  </el-table-column>
                </el-table>
              </el-tab-pane>

              <!-- Kline Tab — Professional Chart with Timeframe -->
              <el-tab-pane label="K线图" name="kline">
                <div v-loading="klinesLoading">
                  <div v-if="klines.length > 0" class="kline-chart-wrapper">
                    <div class="kline-toolbar">
                      <el-radio-group v-model="klineTimeframe" size="small" @change="onTimeframeChange">
                        <el-radio-button value="daily">日线</el-radio-button>
                        <el-radio-button value="weekly">周线</el-radio-button>
                        <el-radio-button value="monthly">月线</el-radio-button>
                        <el-radio-button value="quarterly">季线</el-radio-button>
                        <el-radio-button value="yearly">年线</el-radio-button>
                      </el-radio-group>
                      <span v-if="klineTimeframe === 'daily'" class="ma-legend">
                        <span style="color:#f5c842">MA5</span>
                        <span style="color:#42a5f5">MA10</span>
                        <span style="color:#ab47bc">MA20</span>
                        <span style="color:#ef5350">MA30</span>
                      </span>
                    </div>
                    <div ref="klineChartRef" class="kline-chart"></div>
                    <div class="kline-summary">
                      共 {{ computedKlines.length }} 条{{ {daily:'日',weekly:'周',monthly:'月',quarterly:'季',yearly:'年'}[klineTimeframe] }}线数据 |
                      {{ computedKlines[0]?.date }} ~ {{ computedKlines[computedKlines.length - 1]?.date }}
                    </div>
                  </div>
                  <el-empty v-else description="暂无K线数据" />
                </div>
              </el-tab-pane>

              <!-- AI Analysis Tab -->
              <el-tab-pane label="AI智能诊断" name="ai-analysis">
                <div class="ai-analysis-panel">
                  <!-- AI service status -->
                  <el-alert
                    v-if="aiDsaReachable === false"
                    title="AI分析未配置"
                    description="请在「配置管理」页面配置 LLM 服务商（支持 DeepSeek、通义千问、Moonshot、智谱等国内大模型）。"
                    type="warning"
                    :closable="false"
                    show-icon
                    style="margin-bottom: 16px"
                  />

                  <!-- Action bar -->
                  <div class="ai-action-bar">
                    <el-button
                      type="primary"
                      @click="startAiAnalysis(false)"
                      :loading="aiLoading"
                      :disabled="aiDsaReachable === false"
                      size="default"
                    >
                      开始AI分析
                    </el-button>
                    <el-button
                      @click="startAiAnalysis(true)"
                      :loading="aiLoading"
                      :disabled="aiDsaReachable === false"
                      size="default"
                    >
                      强制重新分析
                    </el-button>
                    <el-tag v-if="aiLoading" type="primary" effect="plain" style="margin-left: 12px">
                      分析中，请稍候...
                    </el-tag>
                  </div>

                  <!-- Error -->
                  <el-alert
                    v-if="aiError"
                    :title="aiError"
                    type="error"
                    closable
                    show-icon
                    style="margin-top: 12px"
                    @close="aiError = ''"
                  />

                  <!-- Report display -->
                  <div v-if="aiReport" class="ai-report" style="margin-top: 16px">

                    <!-- Report Meta -->
                    <div class="ai-report-meta" v-if="aiReport.meta">
                      <span>{{ aiReport.meta.stock_name || selectedStockName }} ({{ aiReport.meta.stock_code || selectedStockCode }})</span>
                      <span v-if="aiReport.meta.current_price" style="margin-left: 16px">
                        现价: <strong :class="(aiReport.meta.change_pct || 0) >= 0 ? 'price-up' : 'price-down'">{{ formatPrice(aiReport.meta.current_price) }}</strong>
                        <span :class="(aiReport.meta.change_pct || 0) >= 0 ? 'price-up' : 'price-down'" style="margin-left: 4px">{{ formatPct(aiReport.meta.change_pct) }}</span>
                      </span>
                      <span v-if="aiReport.meta.created_at" style="margin-left: 16px; color: #909399; font-size: 12px">
                        分析时间: {{ formatTz(aiReport.meta.created_at) }}
                      </span>
                      <span v-if="aiReport.meta.model_used" style="margin-left: 12px; color: #909399; font-size: 12px">
                        模型: {{ aiReport.meta.model_used }}
                      </span>
                    </div>

                    <!-- Summary Section -->
                    <el-row :gutter="16" style="margin-top: 16px" v-if="aiReport.summary">
                      <!-- Left: Key conclusions -->
                      <el-col :span="16">
                        <el-card shadow="hover" class="ai-card">
                          <template #header>
                            <span class="card-title">分析概览</span>
                          </template>
                          <div class="ai-summary-text">{{ aiReport.summary.analysis_summary || '暂无分析结论' }}</div>
                          <el-row :gutter="16" style="margin-top: 16px">
                            <el-col :span="12">
                              <div class="ai-metric">
                                <span class="ai-metric-label">操作建议</span>
                                <el-tag :type="adviceTagType(aiReport.summary.operation_advice)" size="large" effect="dark" style="font-size: 16px; padding: 8px 20px">
                                  {{ aiReport.summary.operation_advice || '-' }}
                                </el-tag>
                              </div>
                            </el-col>
                            <el-col :span="12">
                              <div class="ai-metric">
                                <span class="ai-metric-label">趋势预测</span>
                                <span class="ai-trend">
                                  <span class="ai-trend-icon">{{ trendIcon(aiReport.summary.trend_prediction) }}</span>
                                  {{ aiReport.summary.trend_prediction || '-' }}
                                </span>
                              </div>
                            </el-col>
                          </el-row>
                        </el-card>
                      </el-col>
                      <!-- Right: Sentiment gauge -->
                      <el-col :span="8">
                        <el-card shadow="hover" class="ai-card ai-sentiment-card">
                          <template #header>
                            <span class="card-title">情绪评分</span>
                          </template>
                          <div class="ai-sentiment-gauge">
                            <el-progress
                              type="dashboard"
                              :percentage="aiReport.summary.sentiment_score || 0"
                              :color="sentimentColor(aiReport.summary.sentiment_score)"
                              :width="140"
                              :stroke-width="12"
                            >
                              <template #default="{ percentage }">
                                <div class="ai-gauge-inner">
                                  <span class="ai-gauge-number">{{ percentage }}</span>
                                  <span class="ai-gauge-label">{{ sentimentLabel(aiReport.summary.sentiment_score) }}</span>
                                </div>
                              </template>
                            </el-progress>
                          </div>
                        </el-card>
                      </el-col>
                    </el-row>

                    <!-- Strategy Section -->
                    <el-card shadow="hover" class="ai-card" style="margin-top: 16px" v-if="aiReport.strategy">
                      <template #header>
                        <span class="card-title">策略点位</span>
                      </template>
                      <el-row :gutter="16">
                        <el-col :span="6">
                          <div class="ai-strategy-item ai-strategy-buy">
                            <span class="ai-strategy-label">理想买入</span>
                            <span class="ai-strategy-value">{{ aiReport.strategy.ideal_buy || '-' }}</span>
                          </div>
                        </el-col>
                        <el-col :span="6">
                          <div class="ai-strategy-item ai-strategy-secondary">
                            <span class="ai-strategy-label">次选买入</span>
                            <span class="ai-strategy-value">{{ aiReport.strategy.secondary_buy || '-' }}</span>
                          </div>
                        </el-col>
                        <el-col :span="6">
                          <div class="ai-strategy-item ai-strategy-stop">
                            <span class="ai-strategy-label">止损价位</span>
                            <span class="ai-strategy-value">{{ aiReport.strategy.stop_loss || '-' }}</span>
                          </div>
                        </el-col>
                        <el-col :span="6">
                          <div class="ai-strategy-item ai-strategy-profit">
                            <span class="ai-strategy-label">止盈价位</span>
                            <span class="ai-strategy-value">{{ aiReport.strategy.take_profit || '-' }}</span>
                          </div>
                        </el-col>
                      </el-row>
                    </el-card>

                    <!-- News Content -->
                    <el-card shadow="hover" class="ai-card" style="margin-top: 16px" v-if="aiReport.details && aiReport.details.news_content">
                      <template #header>
                        <span class="card-title">相关资讯</span>
                      </template>
                      <div class="ai-news-content" style="white-space: pre-wrap; line-height: 1.8; color: #606266; font-size: 13px">{{ aiReport.details.news_content }}</div>
                    </el-card>

                    <!-- Details (collapsible) -->
                    <el-card shadow="hover" class="ai-card" style="margin-top: 16px" v-if="aiReport.details">
                      <template #header>
                        <div class="card-header-row">
                          <span class="card-title">详细数据</span>
                          <el-button size="small" text @click="aiShowDetails = !aiShowDetails">
                            {{ aiShowDetails ? '收起' : '展开' }}
                          </el-button>
                        </div>
                      </template>
                      <div v-if="aiShowDetails">
                        <div v-if="aiReport.details.context_snapshot" style="margin-bottom: 16px">
                          <div style="font-weight: 600; margin-bottom: 8px; color: #303133; font-size: 13px">分析上下文快照</div>
                          <pre class="ai-json-viewer">{{ JSON.stringify(aiReport.details.context_snapshot, null, 2) }}</pre>
                        </div>
                        <div v-if="aiReport.details.financial_report" style="margin-bottom: 16px">
                          <div style="font-weight: 600; margin-bottom: 8px; color: #303133; font-size: 13px">财务数据</div>
                          <pre class="ai-json-viewer">{{ JSON.stringify(aiReport.details.financial_report, null, 2) }}</pre>
                        </div>
                        <div v-if="aiReport.details.dividend_metrics" style="margin-bottom: 16px">
                          <div style="font-weight: 600; margin-bottom: 8px; color: #303133; font-size: 13px">分红指标</div>
                          <pre class="ai-json-viewer">{{ JSON.stringify(aiReport.details.dividend_metrics, null, 2) }}</pre>
                        </div>
                        <div v-if="aiReport.details.raw_result">
                          <div style="font-weight: 600; margin-bottom: 8px; color: #303133; font-size: 13px">原始分析结果</div>
                          <pre class="ai-json-viewer">{{ JSON.stringify(aiReport.details.raw_result, null, 2) }}</pre>
                        </div>
                      </div>
                      <div v-else style="color: #909399; font-size: 13px">点击展开查看分析上下文、财务数据、原始结果等详细信息</div>
                    </el-card>
                  </div>

                  <!-- History -->
                  <el-card shadow="hover" class="ai-card" style="margin-top: 16px" v-if="aiHistory.length > 0">
                    <template #header>
                      <div class="card-header-row">
                        <span class="card-title">历史分析记录</span>
                        <el-button size="small" @click="loadAiHistory" :loading="aiHistoryLoading" :icon="Refresh">刷新</el-button>
                      </div>
                    </template>
                    <el-table :data="aiHistory" stripe border size="small" style="width: 100%">
                      <el-table-column prop="created_at" label="时间" width="180">
                        <template #default="{ row }">
                          {{ row.created_at ? formatTz(row.created_at) : '-' }}
                        </template>
                      </el-table-column>
                      <el-table-column prop="sentiment_score" label="情绪" width="90">
                        <template #default="{ row }">
                          <el-tag :color="sentimentColor(row.sentiment_score)" effect="dark" size="small" style="color: #fff; border: none">
                            {{ row.sentiment_score ?? '-' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="operation_advice" label="建议" width="90">
                        <template #default="{ row }">
                          <el-tag :type="adviceTagType(row.operation_advice)" size="small">
                            {{ row.operation_advice || '-' }}
                          </el-tag>
                        </template>
                      </el-table-column>
                      <el-table-column prop="report_type" label="类型" width="80" />
                      <el-table-column label="操作" width="100">
                        <template #default="{ row }">
                          <el-button size="small" type="primary" text @click="loadAiReport(row.id)">查看</el-button>
                        </template>
                      </el-table-column>
                    </el-table>
                  </el-card>

                  <!-- Empty state -->
                  <el-empty
                    v-if="!aiLoading && !aiReport && !aiError && aiHistory.length === 0"
                    description="点击「开始AI分析」对当前股票进行智能诊断"
                    :image-size="80"
                    style="margin-top: 40px"
                  />
                </div>
              </el-tab-pane>

              <!-- ── Tab 4: 公司公告 ── -->
              <el-tab-pane label="公司公告" name="news">
                <div v-loading="newsLoading">
                  <div v-if="newsData.length > 0" class="news-timeline">
                    <el-timeline>
                      <el-timeline-item
                        v-for="(item, idx) in newsData"
                        :key="idx"
                        :timestamp="item.date"
                        placement="top"
                        type="warning"
                      >
                        <el-card shadow="hover" style="margin-bottom: 0">
                          <div style="display: flex; align-items: flex-start; gap: 8px">
                            <el-tag v-if="item.category" type="warning" size="small" style="flex-shrink: 0">
                              {{ item.category }}
                            </el-tag>
                            <div style="flex: 1; min-width: 0">
                              <div style="font-weight: 500; margin-bottom: 4px">
                                <a v-if="item.url" :href="item.url" target="_blank" rel="noopener"
                                   style="color: inherit; text-decoration: none"
                                   @mouseover="$event.target.style.color='#409EFF'"
                                   @mouseout="$event.target.style.color='inherit'">
                                  {{ item.title }}
                                </a>
                                <span v-else>{{ item.title }}</span>
                              </div>
                            </div>
                          </div>
                        </el-card>
                      </el-timeline-item>
                    </el-timeline>
                    <div style="display: flex; justify-content: center; margin-top: 16px">
                      <el-pagination
                        v-model:current-page="newsPage"
                        :page-size="newsPageSize"
                        :total="newsTotal"
                        layout="prev, pager, next, total"
                        :small="true"
                        @current-change="loadStockNews"
                      />
                    </div>
                  </div>
                  <el-empty v-else-if="!newsLoading" description="暂无公告数据，点击刷新加载" :image-size="80">
                    <el-button type="primary" size="small" @click="loadStockNews(1)" :icon="Refresh">加载公告</el-button>
                  </el-empty>
                </div>
              </el-tab-pane>

              <!-- ── Tab 5: 相关评论 (散户情绪) ── -->
              <el-tab-pane label="散户情绪" name="sentiment">
                <div v-loading="sentimentLoading">
                  <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 16px">
                    <span style="font-size: 13px; color: #606266">统计天数:</span>
                    <el-radio-group v-model="sentimentDays" size="small" @change="loadSentiment">
                      <el-radio-button :value="7">7天</el-radio-button>
                      <el-radio-button :value="14">14天</el-radio-button>
                      <el-radio-button :value="30">30天</el-radio-button>
                      <el-radio-button :value="60">60天</el-radio-button>
                    </el-radio-group>
                    <el-button size="small" @click="loadSentiment" :icon="Refresh" :loading="sentimentLoading">刷新</el-button>
                    <div style="flex: 1"></div>
                    <el-tooltip content="数据来源: 东方财富股吧。情绪分数通过关键词分析帖子标题计算，范围0-100 (0=极度看空, 50=中性, 100=极度看多)。看多=红色(涨), 看空=绿色(跌)。独立用户数已按用户ID去重。" placement="top-end">
                      <el-icon style="color: #909399; cursor: help; font-size: 16px"><InfoFilled /></el-icon>
                    </el-tooltip>
                  </div>

                  <div v-if="sentimentData.length > 0">
                    <!-- Chart -->
                    <div ref="sentimentChartRef" style="width: 100%; height: 380px; margin-bottom: 20px"></div>

                    <!-- Summary stats -->
                    <el-row :gutter="16" style="margin-bottom: 16px">
                      <el-col :span="6">
                        <el-statistic title="最新情绪分数">
                          <template #default>
                            <span :style="{ color: sentimentData[sentimentData.length-1]?.sentiment_score >= 50 ? '#F56C6C' : '#67C23A', fontWeight: 'bold' }">
                              {{ sentimentData[sentimentData.length-1]?.sentiment_score ?? '-' }}
                            </span>
                          </template>
                        </el-statistic>
                      </el-col>
                      <el-col :span="6">
                        <el-statistic title="平均情绪分数">
                          <template #default>
                            {{ sentimentData.length > 0 ? Math.round(sentimentData.reduce((s,d) => s + d.sentiment_score, 0) / sentimentData.length) : '-' }}
                          </template>
                        </el-statistic>
                      </el-col>
                      <el-col :span="6">
                        <el-statistic title="日均独立用户">
                          <template #default>
                            {{ sentimentData.length > 0 ? Math.round(sentimentData.reduce((s,d) => s + d.comment_count, 0) / sentimentData.length) : '-' }}
                          </template>
                        </el-statistic>
                      </el-col>
                      <el-col :span="6">
                        <el-statistic title="日均帖子数">
                          <template #default>
                            {{ sentimentData.length > 0 ? Math.round(sentimentData.reduce((s,d) => s + d.total_posts, 0) / sentimentData.length) : '-' }}
                          </template>
                        </el-statistic>
                      </el-col>
                    </el-row>

                    <!-- Detail table -->
                    <el-table :data="[...sentimentData].reverse()" stripe border size="small" style="width: 100%" max-height="300">
                      <el-table-column prop="date" label="日期" width="120" />
                      <el-table-column prop="sentiment_score" label="情绪分数" width="110" sortable>
                        <template #default="{ row }">
                          <el-progress
                            :percentage="row.sentiment_score"
                            :color="row.sentiment_score >= 60 ? '#F56C6C' : row.sentiment_score >= 40 ? '#E6A23C' : '#67C23A'"
                            :stroke-width="14"
                            :text-inside="true"
                            style="width: 80px"
                          />
                        </template>
                      </el-table-column>
                      <el-table-column prop="comment_count" label="独立用户" width="100" sortable />
                      <el-table-column prop="total_posts" label="帖子总数" width="100" sortable />
                      <el-table-column prop="avg_read_count" label="平均阅读" width="100" />
                      <el-table-column prop="avg_reply_count" label="平均回复" width="100" />
                      <el-table-column prop="source" label="来源" width="140" />
                    </el-table>
                  </div>

                  <el-empty v-else-if="!sentimentLoading" description="暂无评论数据，点击刷新加载" :image-size="80">
                    <el-button type="primary" size="small" @click="loadSentiment" :icon="Refresh">加载散户情绪</el-button>
                  </el-empty>
                </div>
              </el-tab-pane>

            </el-tabs>
          </el-card>

          <el-empty v-if="!selectedStockId" description="请先选择一只股票查看行情数据" />
        </div>

        <!-- ==================== Section 3: Fetch Logs ==================== -->
        <div v-if="activeMenu === 'logs'">
          <div class="section-header">
            <h2>抓取日志</h2>
            <div>
              <el-button type="danger" plain size="small" @click="deleteAllLogs" :loading="deleteLogsLoading" style="margin-right: 8px;">
                清除所有日志
              </el-button>
              <el-button type="warning" plain size="small" @click="deleteErrorLogs" :loading="deleteLogsLoading" style="margin-right: 8px;">
                清除错误日志
              </el-button>
              <el-button type="primary" @click="loadLogs" :icon="Refresh" :loading="logsLoading">
                刷新
              </el-button>
            </div>
          </div>

          <el-card class="section-card" shadow="hover">
            <el-table
              :data="logs"
              v-loading="logsLoading"
              stripe
              border
              style="width: 100%"
              empty-text="暂无日志记录"
              max-height="600"
            >
              <el-table-column prop="created_at" label="时间" width="200">
                <template #default="{ row }">
                  {{ row.created_at ? formatTz(row.created_at) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="stock_code" label="股票" width="160">
                <template #default="{ row }">
                  <a v-if="row.stock_code" class="stock-link" @click="navigateToStock({ code: row.stock_code })">{{ row.stock_code }} {{ row.stock_name || '' }}</a>
                  <span v-else-if="row.fetch_type && row.fetch_type.includes('summary')" style="color: #409eff; font-weight: 500;">汇总</span>
                  <span v-else style="color: #999">-</span>
                </template>
              </el-table-column>
              <el-table-column prop="fetch_type" label="类型" width="140">
                <template #default="{ row }">
                  <el-tag size="small" :type="fetchTypeTagType(row.fetch_type)">{{ fetchTypeLabel(row.fetch_type) }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="source" label="数据源" min-width="180">
                <template #default="{ row }">
                  <span style="font-size: 12px; color: #606266;">{{ row.source || '-' }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="status" label="状态" width="100">
                <template #default="{ row }">
                  <el-tag
                    :type="row.status === 'success' ? 'success' : row.status === 'error' ? 'danger' : row.status === 'partial' ? 'warning' : 'info'"
                    size="small"
                  >
                    {{ row.status || '-' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="message" label="消息" min-width="300" show-overflow-tooltip />
            </el-table>
          </el-card>
        </div>

        <!-- ==================== Section 4: Smart Screener ==================== -->
        <div v-if="activeMenu === 'screener'">
          <div class="section-header">
            <h2>智能选股</h2>
          </div>

          <!-- Preset Strategies -->
          <el-card class="section-card" shadow="hover">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">预设策略</span>
                <el-tooltip content="预设策略基于常见的选股思路，从基本面和技术面预定义筛选条件，一键选股。数据来源于全A股的日K线和财务数据。" placement="top-end">
                  <el-icon style="color: #909399; cursor: help; font-size: 16px"><InfoFilled /></el-icon>
                </el-tooltip>
              </div>
            </template>
            <div class="preset-grid">
              <el-button
                v-for="p in presets"
                :key="p.key"
                :type="activePreset === p.key ? 'primary' : 'default'"
                @click="runPresetScreen(p.key)"
                :loading="screenLoading && activePreset === p.key"
                class="preset-btn"
              >
                <div class="preset-btn-content">
                  <strong>{{ p.name }}</strong>
                  <small>{{ p.description }}</small>
                </div>
              </el-button>
            </div>
          </el-card>

          <!-- Custom Filter -->
          <el-card class="section-card" shadow="hover">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">自定义筛选</span>
                <div style="display: flex; align-items: center; gap: 12px;">
                  <el-button type="primary" @click="runCustomScreen" :loading="screenLoading && !activePreset" size="small">
                    开始筛选
                  </el-button>
                  <el-tooltip content="自由组合基本面(市值/PE/PB/行业)和技术面(价格位置/量能变化/形态检测)条件，对全A股进行筛选。所有指标基于过去250个交易日(约1年)的日K线数据计算。" placement="top-end">
                    <el-icon style="color: #909399; cursor: help; font-size: 16px"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </div>
            </template>
            <el-row :gutter="16">
              <el-col :span="6">
                <div>
                  <el-select v-model="screenIndustryTemp" placeholder="搜索添加行业(+包含/-排除)" filterable clearable style="width: 100%" @change="addScreenIndustry">
                    <el-option v-for="ind in screenIndustryAvailable" :key="ind" :label="ind" :value="ind" />
                  </el-select>
                  <div v-if="screenForm.industries.length || screenForm.exclude_industries.length" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px;">
                    <el-tag v-for="ind in screenForm.industries" :key="'+'+ind" type="success" closable size="small" @click="toggleScreenIndustry(ind)" @close="removeScreenIndustry(ind)" style="cursor: pointer;">+{{ ind }}</el-tag>
                    <el-tag v-for="ind in screenForm.exclude_industries" :key="'-'+ind" type="danger" closable size="small" @click="toggleScreenIndustry(ind)" @close="removeScreenIndustry(ind)" style="cursor: pointer;">-{{ ind }}</el-tag>
                  </div>
                </div>
              </el-col>
              <el-col :span="3">
                <el-input v-model="screenForm.min_market_cap" placeholder="最小市值(亿)" type="number" />
              </el-col>
              <el-col :span="3">
                <el-input v-model="screenForm.max_market_cap" placeholder="最大市值(亿)" type="number" />
              </el-col>
              <el-col :span="3">
                <el-input v-model="screenForm.max_pe" placeholder="最大PE" type="number" />
              </el-col>
              <el-col :span="3">
                <el-input v-model="screenForm.max_pb" placeholder="最大PB" type="number" />
              </el-col>
              <el-col :span="3">
                <el-input v-model="screenForm.max_price_percentile" placeholder="价格百分位上限%" type="number" />
              </el-col>
              <el-col :span="3">
                <el-select v-model="screenForm.sort_by" style="width: 100%">
                  <el-option label="市值降序" value="market_cap_desc" />
                  <el-option label="市值升序" value="market_cap_asc" />
                  <el-option label="PE升序" value="pe_asc" />
                  <el-option label="PB升序" value="pb_asc" />
                  <el-option label="价格低位优先" value="price_pct_asc" />
                  <el-option label="放量排序" value="volume_surge_desc" />
                  <el-option label="形态评分" value="pattern_score_desc" />
                </el-select>
              </el-col>
            </el-row>
            <el-row :gutter="16" style="margin-top: 12px">
              <el-col :span="6">
                <el-checkbox v-model="screenForm.enable_volume_pattern">
                  启用"放量上涨→缩量回调"形态检测
                </el-checkbox>
              </el-col>
            </el-row>
            <!-- Volume Pattern Parameters (collapsible) -->
            <div v-if="screenForm.enable_volume_pattern" class="vp-params" style="margin-top: 12px; padding: 16px; background: #f5f7fa; border-radius: 6px; border: 1px solid #e4e7ed;">
              <div style="font-size: 14px; font-weight: 600; color: #303133; margin-bottom: 12px;">形态参数自定义</div>

              <!-- Row 1: 放量阶段 -->
              <div class="vp-section-title">放量上涨阶段</div>
              <el-row :gutter="16" style="margin-bottom: 12px">
                <el-col :span="4">
                  <div class="vp-param-label">回溯几个月</div>
                  <div class="vp-param-hint">往前看多久的K线数据来检测形态</div>
                  <el-input-number v-model="screenForm.vp_lookback_months" :min="1" :max="24" :step="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">至少连续放量几周</div>
                  <div class="vp-param-hint">周成交量连续超过基准量的最少周数</div>
                  <el-input-number v-model="screenForm.vp_min_surge_weeks" :min="2" :max="10" :step="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">放量倍数 (x基准量)</div>
                  <div class="vp-param-hint">周成交量须达到放量前4周均量的N倍</div>
                  <el-input-number v-model="screenForm.vp_surge_vol_ratio" :min="1.0" :max="5.0" :step="0.1" :precision="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">期间最少涨幅 %</div>
                  <div class="vp-param-hint">放量期间价格累计至少涨多少</div>
                  <el-input-number v-model="screenForm.vp_min_surge_gain" :min="0" :max="50" :step="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">期间最多涨幅 %</div>
                  <div class="vp-param-hint">排除涨幅过大(主升浪已走完)</div>
                  <el-input-number v-model="screenForm.vp_max_surge_gain" :min="5" :max="100" :step="5" size="small" controls-position="right" style="width: 100%" />
                </el-col>
              </el-row>

              <!-- Row 2: 回调缩量阶段 -->
              <div class="vp-section-title">缩量回调阶段</div>
              <el-row :gutter="16" style="margin-bottom: 12px">
                <el-col :span="4">
                  <div class="vp-param-label">回调至少几周</div>
                  <div class="vp-param-hint">放量结束后至少经过几周才算有效回调</div>
                  <el-input-number v-model="screenForm.vp_min_pullback_weeks" :min="1" :max="20" :step="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">回调跌幅至少 %</div>
                  <div class="vp-param-hint">从最高点回落至少多少才算回调</div>
                  <el-input-number v-model="screenForm.vp_min_pullback_pct" :min="0" :max="30" :step="1" :precision="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">5日总量/周最低量上限</div>
                  <div class="vp-param-hint">最近5日成交量之和 vs 回溯期最低周量的倍数上限</div>
                  <el-input-number v-model="screenForm.vp_recent_5d_vs_minweek_max" :min="0.1" :max="10" :step="0.1" :precision="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">最新1日/日最低量上限</div>
                  <div class="vp-param-hint">最新1日量 vs 回溯期日最低量的倍数上限; &lt;1表示须低于最低量</div>
                  <el-input-number v-model="screenForm.vp_latest_vs_minday_max" :min="0.1" :max="10" :step="0.1" :precision="1" size="small" controls-position="right" style="width: 100%" />
                </el-col>
                <el-col :span="4">
                  <div class="vp-param-label">最低综合评分</div>
                  <div class="vp-param-hint">综合打分(0-100)低于此值不入选</div>
                  <el-input-number v-model="screenForm.vp_min_score" :min="0" :max="100" :step="5" size="small" controls-position="right" style="width: 100%" />
                </el-col>
              </el-row>
            </div>
          </el-card>

          <!-- Results -->
          <el-card class="section-card" shadow="hover" v-if="screenResults.length > 0 || screenLoading">
            <template #header>
              <div class="card-header-row">
                <div style="display: flex; align-items: center; gap: 6px;">
                  <span class="card-title">筛选结果 ({{ screenResults.length }} 只{{ screenTotal > screenResults.length ? '，共匹配 ' + screenTotal + ' 只' : '' }})</span>
                  <el-tooltip placement="top">
                    <template #content>
                      <div style="max-width: 360px; line-height: 1.6;">
                        <div style="font-weight: 600; margin-bottom: 6px;">指标说明</div>
                        <div><b>价格百分位</b>: 当前价在250日价格区间中的位置，0%=1年最低, 100%=1年最高</div>
                        <div><b>量比</b>: 近5日均量 / 过去60日均量，>2=放量, <0.5=缩量</div>
                        <div><b>周涨跌</b>: 最近5个交易日的涨跌幅</div>
                        <div><b>PE(TTM)</b>: 滚动市盈率 = 股价 / 每股收益(最近12个月)</div>
                        <div><b>PB</b>: 市净率 = 股价 / 每股净资产</div>
                        <div v-if="screenForm.enable_volume_pattern" style="margin-top: 4px; border-top: 1px solid rgba(255,255,255,0.2); padding-top: 4px;">
                          <div><b>形态评分</b>: 综合打分(0-100)，评估"放量上涨→缩量回调"形态质量</div>
                          <div><b>放量倍数</b>: 放量期间峰值周量 / 放量前4周均量</div>
                          <div><b>5日/周最低</b>: 近5日总量 / 回溯期最低周量，越低缩量越明显</div>
                          <div><b>最新日/日最低</b>: 最新1日量 / 期间最低日量，越低越极端缩量</div>
                        </div>
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
                <el-popover placement="bottom-end" :width="260" trigger="click">
                  <template #reference>
                    <el-button :icon="Setting" size="small" circle title="列设置" />
                  </template>
                  <div style="font-weight: 500; margin-bottom: 8px;">显示列</div>
                  <el-checkbox-group v-model="screenVisibleCols">
                    <div v-for="c in screenColDefs.filter(d => !d.fixed && (!d.vp || screenForm.enable_volume_pattern))" :key="c.key"
                      style="line-height: 26px;">
                      <el-checkbox :value="c.key">{{ c.label }}</el-checkbox>
                    </div>
                  </el-checkbox-group>
                </el-popover>
              </div>
            </template>
            <div class="screener-table-wrap">
            <el-table
              :data="paginatedScreenResults"
              v-loading="screenLoading"
              stripe
              border
              style="width: 100%"
              empty-text="暂无符合条件的股票"
              max-height="600"
              :scrollbar-always-on="true"
              :default-sort="{ prop: 'total_market_cap', order: 'descending' }"
            >
              <el-table-column prop="code" label="代码" width="80" sortable>
                <template #default="{ row }">
                  <a class="stock-link" style="font-family: monospace;" @click="navigateToStock(row)">{{ row.code }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="name" label="名称" min-width="90">
                <template #default="{ row }">
                  <a class="stock-link" @click="navigateToStock(row)">{{ row.name }}</a>
                </template>
              </el-table-column>
              <el-table-column prop="industry" label="行业" min-width="95" v-if="showCol('industry')" sortable />
              <el-table-column prop="latest_close" label="最新价" min-width="82" v-if="showCol('latest_close')" sortable>
                <template #default="{ row }">
                  {{ row.latest_close ? row.latest_close.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="total_market_cap" label="总市值" min-width="90" v-if="showCol('total_market_cap')" sortable>
                <template #default="{ row }">
                  {{ formatMcap(row.total_market_cap) }}
                </template>
              </el-table-column>
              <el-table-column prop="pe_ttm" label="PE(TTM)" min-width="92" v-if="showCol('pe_ttm')" sortable>
                <template #default="{ row }">
                  {{ row.pe_ttm ? row.pe_ttm.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="pb" label="PB" min-width="68" v-if="showCol('pb')" sortable>
                <template #default="{ row }">
                  {{ row.pb ? row.pb.toFixed(2) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="price_percentile" label="价格百分位" min-width="115" v-if="showCol('price_percentile')" sortable>
                <template #header>
                  <span>价格百分位</span>
                  <el-tooltip content="当前价在过去250个交易日(约1年)价格区间中的位置。公式: (当前价-区间最低)/(区间最高-区间最低)×100。0%=1年最低价, 100%=1年最高价。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span v-if="row.price_percentile !== null && row.price_percentile !== undefined">
                    <el-progress
                      :percentage="Math.round(row.price_percentile)"
                      :color="row.price_percentile < 30 ? '#67c23a' : row.price_percentile > 70 ? '#f56c6c' : '#e6a23c'"
                      :stroke-width="14"
                      :text-inside="true"
                      style="width: 80px"
                    />
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="volume_surge_ratio" label="量比" min-width="80" v-if="showCol('volume_surge_ratio')" sortable>
                <template #header>
                  <span>量比</span>
                  <el-tooltip content="近5日平均成交量 / 过去60日平均成交量。>2表示显著放量(可能突破), <0.5表示明显缩量(可能蓄势)。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span :style="{ color: row.volume_surge_ratio > 2 ? '#f56c6c' : row.volume_surge_ratio < 0.5 ? '#67c23a' : '' }">
                    {{ formatRatio(row.volume_surge_ratio) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="weekly_change_pct" label="周涨跌" min-width="82" v-if="showCol('weekly_change_pct')" sortable>
                <template #default="{ row }">
                  <span :class="row.weekly_change_pct > 0 ? 'price-up' : row.weekly_change_pct < 0 ? 'price-down' : ''">
                    {{ formatPctValue(row.weekly_change_pct) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column prop="volume_pattern_score" label="形态评分" min-width="110" v-if="screenForm.enable_volume_pattern && showCol('volume_pattern_score')" sortable>
                <template #header>
                  <span>形态评分</span>
                  <el-tooltip content="综合评分(0-100)，衡量'放量上涨→缩量回调'形态的质量。包含4个子项：放量质量(30分)、涨幅合理度(20分)、回调质量(25分)、缩量程度(25分)。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span v-if="row.volume_pattern_score != null">
                    <el-progress
                      :percentage="Math.round(row.volume_pattern_score)"
                      :color="row.volume_pattern_score >= 70 ? '#67c23a' : row.volume_pattern_score >= 50 ? '#e6a23c' : '#909399'"
                      :stroke-width="14"
                      :text-inside="true"
                      style="width: 80px"
                    />
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="surge_weeks_count" label="放量周数" min-width="88" v-if="screenForm.enable_volume_pattern && showCol('surge_weeks_count')" sortable>
                <template #default="{ row }">
                  {{ row.surge_weeks_count != null ? row.surge_weeks_count + '周' : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="max_surge_vol_ratio" label="放量倍数" min-width="100" v-if="screenForm.enable_volume_pattern && showCol('max_surge_vol_ratio')" sortable>
                <template #header>
                  <span>放量倍数</span>
                  <el-tooltip content="放量期间峰值周成交量 / 放量前4周平均周成交量。倍数越高说明资金介入越明显。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span v-if="row.max_surge_vol_ratio != null" :style="{ color: row.max_surge_vol_ratio >= 2 ? '#f56c6c' : '' }">
                    {{ row.max_surge_vol_ratio.toFixed(2) }}x
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="surge_price_gain" label="放量涨幅" min-width="90" v-if="screenForm.enable_volume_pattern && showCol('surge_price_gain')" sortable>
                <template #default="{ row }">
                  <span v-if="row.surge_price_gain != null" class="price-up">
                    +{{ row.surge_price_gain.toFixed(1) }}%
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="pullback_weeks" label="回调周数" min-width="88" v-if="screenForm.enable_volume_pattern && showCol('pullback_weeks')" sortable>
                <template #default="{ row }">
                  {{ row.pullback_weeks != null ? row.pullback_weeks + '周' : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="pullback_pct" label="回调幅度" min-width="90" v-if="screenForm.enable_volume_pattern && showCol('pullback_pct')" sortable>
                <template #default="{ row }">
                  <span v-if="row.pullback_pct != null" class="price-down">
                    -{{ row.pullback_pct.toFixed(1) }}%
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="recent_5d_vs_minweek" label="5日/周最低" min-width="112" v-if="screenForm.enable_volume_pattern && showCol('recent_5d_vs_minweek')" sortable>
                <template #header>
                  <span>5日/周最低</span>
                  <el-tooltip content="最近5个交易日成交量之和 / 回溯期内最低周成交量。越接近或低于1.0说明当前缩量越极致。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span v-if="row.recent_5d_vs_minweek != null" :style="{ color: row.recent_5d_vs_minweek <= 1 ? '#67c23a' : row.recent_5d_vs_minweek <= 1.5 ? '#e6a23c' : '#f56c6c' }">
                    {{ row.recent_5d_vs_minweek.toFixed(2) }}x
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="latest_vs_minday" label="最新日/日最低" min-width="130" v-if="screenForm.enable_volume_pattern && showCol('latest_vs_minday')" sortable>
                <template #header>
                  <span>最新日/日最低</span>
                  <el-tooltip content="最新1日成交量 / 回溯期内最低日成交量。越接近1.0说明当前日成交量接近历史地量。<1表示已低于历史最低量。" placement="top">
                    <el-icon style="color: #c0c4cc; cursor: help; margin-left: 2px; vertical-align: middle;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </template>
                <template #default="{ row }">
                  <span v-if="row.latest_vs_minday != null" :style="{ color: row.latest_vs_minday <= 1 ? '#67c23a' : row.latest_vs_minday <= 1.5 ? '#e6a23c' : '#f56c6c' }">
                    {{ row.latest_vs_minday.toFixed(2) }}x
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="recent_vol_percentile" label="近期量位" min-width="90" v-if="screenForm.enable_volume_pattern && showCol('recent_vol_percentile')" sortable>
                <template #default="{ row }">
                  <span v-if="row.recent_vol_percentile != null" :style="{ color: row.recent_vol_percentile <= 10 ? '#67c23a' : '' }">
                    {{ row.recent_vol_percentile.toFixed(0) }}%
                  </span>
                  <span v-else>-</span>
                </template>
              </el-table-column>
              <el-table-column prop="base_weekly_vol" label="周基准量" min-width="100" v-if="screenForm.enable_volume_pattern && showCol('base_weekly_vol')" sortable>
                <template #default="{ row }">
                  {{ row.base_weekly_vol != null ? formatVolume(row.base_weekly_vol) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="pullback_min_weekly_vol" label="回调最低周量" min-width="120" v-if="screenForm.enable_volume_pattern && showCol('pullback_min_weekly_vol')" sortable>
                <template #default="{ row }">
                  {{ row.pullback_min_weekly_vol != null ? formatVolume(row.pullback_min_weekly_vol) : '-' }}
                </template>
              </el-table-column>
              <el-table-column prop="min_daily_vol" label="期间日最低量" min-width="120" v-if="screenForm.enable_volume_pattern && showCol('min_daily_vol')" sortable>
                <template #default="{ row }">
                  {{ row.min_daily_vol != null ? formatVolume(row.min_daily_vol) : '-' }}
                </template>
              </el-table-column>
            </el-table>
            </div>
            <div style="display: flex; justify-content: center; margin-top: 16px;">
              <el-pagination
                v-model:current-page="screenPage"
                v-model:page-size="screenPageSize"
                :page-sizes="[20, 50, 100, 200]"
                :total="screenResults.length"
                layout="total, sizes, prev, pager, next, jumper"
                background
              />
            </div>
          </el-card>
        </div>

        <!-- ==================== Section 5: Schedule Settings ==================== -->
        <div v-if="activeMenu === 'schedule'">
          <div class="section-header">
            <h2>定时更新设置</h2>
          </div>

          <!-- Schedule Configuration -->
          <el-card class="section-card" shadow="hover" v-loading="scheduleLoading">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">每日自动更新配置</span>
                <el-tag :type="scheduleSettings.daily_update_enabled ? 'success' : 'info'" size="default">
                  {{ scheduleSettings.daily_update_enabled ? '已启用' : '已停用' }}
                </el-tag>
              </div>
            </template>

            <el-form label-width="120px" style="max-width: 600px">
              <el-form-item label="启用定时更新">
                <el-switch v-model="scheduleSettings.daily_update_enabled" />
              </el-form-item>
              <el-form-item label="更新时间">
                <el-row :gutter="12" align="middle">
                  <el-col :span="8">
                    <el-input-number
                      v-model="scheduleSettings.daily_update_hour"
                      :min="0"
                      :max="23"
                      controls-position="right"
                      style="width: 100%"
                    />
                  </el-col>
                  <el-col :span="2" style="text-align: center; line-height: 32px; font-size: 18px; font-weight: bold;">:</el-col>
                  <el-col :span="8">
                    <el-input-number
                      v-model="scheduleSettings.daily_update_minute"
                      :min="0"
                      :max="59"
                      controls-position="right"
                      style="width: 100%"
                    />
                  </el-col>
                  <el-col :span="6">
                    <span style="color: #909399; font-size: 13px; margin-left: 8px">
                      {{ formatScheduleTime(scheduleSettings.daily_update_hour, scheduleSettings.daily_update_minute) }}
                    </span>
                  </el-col>
                </el-row>
              </el-form-item>
              <el-form-item label="时区">
                <el-select v-model="scheduleSettings.daily_update_timezone" style="width: 100%" filterable allow-create>
                  <el-option
                    v-for="tz in commonTimezones"
                    :key="tz"
                    :label="tz"
                    :value="tz"
                  />
                </el-select>
              </el-form-item>
              <el-form-item>
                <el-button type="primary" @click="saveScheduleSettings" :loading="scheduleSaving">
                  保存设置
                </el-button>
                <el-button type="warning" @click="triggerManualUpdate" :loading="triggerLoading" style="margin-left: 12px">
                  立即执行更新
                </el-button>
              </el-form-item>
            </el-form>

            <el-divider />

            <div style="color: #909399; font-size: 13px; line-height: 2">
              <p><strong>更新内容：</strong>每日自动更新所有活跃股票的最新K线数据、PE/PB/市值等基本面数据。</p>
              <p><strong>查漏补缺：</strong>自动检测缺失的K线数据，在下一次更新时进行补全。</p>
              <p><strong>运行时间：</strong>仅在工作日（周一至周五）运行，每10分钟检查一次是否到达设定的更新时间。</p>
              <p><strong>注意：</strong>需要 Celery Worker 和 Celery Beat 进程运行中才能执行定时任务。</p>
            </div>
          </el-card>

          <!-- Update Status -->
          <el-card class="section-card" shadow="hover" v-if="scheduleStatus">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">更新状态</span>
                <el-button size="small" @click="loadScheduleStatus" :icon="Refresh">刷新</el-button>
              </div>
            </template>

            <el-descriptions :column="2" border size="small">
              <el-descriptions-item label="上次运行">
                {{ scheduleStatus.last_run ? formatTz(scheduleStatus.last_run) : '尚未运行' }}
              </el-descriptions-item>
              <el-descriptions-item label="运行状态">
                <el-tag v-if="scheduleStatus.last_status" :type="statusTagType(scheduleStatus.last_status)" size="small">
                  {{ scheduleStatus.last_status === 'success' ? '成功' :
                     scheduleStatus.last_status === 'partial' ? '部分成功' :
                     scheduleStatus.last_status === 'running' ? '运行中' :
                     scheduleStatus.last_status === 'failed' ? '失败' : scheduleStatus.last_status }}
                </el-tag>
                <span v-else style="color: #909399">-</span>
              </el-descriptions-item>
              <el-descriptions-item label="运行详情" :span="2">
                {{ scheduleStatus.last_message || '-' }}
              </el-descriptions-item>
            </el-descriptions>

            <el-divider content-position="left">数据统计</el-divider>

            <el-row :gutter="20" v-if="scheduleStatus.stats">
              <el-col :span="5">
                <el-statistic title="活跃股票" :value="scheduleStatus.stats.total_active_stocks || 0" />
              </el-col>
              <el-col :span="5">
                <el-statistic title="有K线数据" :value="scheduleStatus.stats.stocks_with_klines || 0" />
              </el-col>
              <el-col :span="5">
                <el-statistic title="K线总行数" :value="scheduleStatus.stats.total_kline_rows || 0" />
              </el-col>
              <el-col :span="5">
                <el-statistic title="有PE数据" :value="scheduleStatus.stats.stocks_with_pe || 0" />
              </el-col>
              <el-col :span="4">
                <div class="el-statistic">
                  <div class="el-statistic__head">最新K线日期</div>
                  <div class="el-statistic__content">
                    <span class="el-statistic__number" style="font-size: 16px">{{ scheduleStatus.stats.latest_kline_date || '-' }}</span>
                  </div>
                </div>
              </el-col>
            </el-row>
          </el-card>
        </div>

        <!-- ==================== Section 6: Configuration Management ==================== -->
        <div v-if="activeMenu === 'config'">
          <div class="section-header">
            <h2>配置管理</h2>
          </div>

          <!-- LLM Configuration -->
          <el-card class="section-card" shadow="hover" v-loading="configLoading">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">AI 分析 (LLM) 配置</span>
                <div>
                  <el-button :loading="configTestLoading" @click="testLLMConnection">
                    测试连接
                  </el-button>
                  <el-button type="primary" :loading="configSaving" @click="saveConfigSettings">
                    保存配置
                  </el-button>
                </div>
              </div>
            </template>

            <el-form label-width="160px" style="max-width: 700px">
              <el-form-item label="服务商预设">
                <el-select v-model="selectedLLMPreset" @change="applyLLMPreset" style="width: 100%"
                  placeholder="选择服务商快速填写配置">
                  <el-option v-for="p in llmProviderPresets" :key="p.value" :label="p.label" :value="p.value" />
                </el-select>
                <div class="form-help" v-if="selectedLLMPreset !== 'custom'" style="color: #409eff">
                  {{ llmProviderPresets.find(p => p.value === selectedLLMPreset)?.hint }}
                </div>
              </el-form-item>

              <el-form-item label="模型名称">
                <el-input v-model="configForm.llm.model" placeholder="openai/gpt-4o-mini 或 deepseek/deepseek-chat">
                  <template #prepend>LITELLM_MODEL</template>
                </el-input>
                <div class="form-help">格式: provider/model-name — 原生支持: deepseek/、dashscope/、moonshot/、volcengine/、openai/、gemini/<br>其他服务商用 openai/ 前缀 + 填写下方 API URL</div>
              </el-form-item>

              <el-form-item label="API Key">
                <el-input
                  v-model="configForm.llm.api_key"
                  :type="showApiKey ? 'text' : 'password'"
                  :placeholder="originalMaskedKey || '输入 API Key'"
                >
                  <template #prepend>API_KEY</template>
                  <template #append>
                    <el-button @click="showApiKey = !showApiKey">
                      {{ showApiKey ? '隐藏' : '显示' }}
                    </el-button>
                  </template>
                </el-input>
                <div class="form-help" v-if="originalMaskedKey">当前: {{ originalMaskedKey }}（留空则不修改）</div>
              </el-form-item>

              <el-form-item label="API URL">
                <el-input v-model="configForm.llm.api_url" placeholder="使用原生支持的服务商时留空">
                  <template #prepend>OPENAI_BASE_URL</template>
                </el-input>
                <div class="form-help">OpenAI 兼容端点地址。DeepSeek/Qwen/Moonshot/Doubao 原生前缀无需填写；智谱/硅基流动等需要填写</div>
              </el-form-item>

              <el-form-item label="温度">
                <el-input-number v-model="configForm.llm.temperature" :min="0" :max="2" :step="0.1" :precision="1" />
                <span class="form-inline-help">值越低越确定，越高越随机</span>
              </el-form-item>

              <el-form-item label="最大 Tokens">
                <el-input-number v-model="configForm.llm.max_tokens" :min="256" :max="128000" :step="1024" />
              </el-form-item>

              <el-form-item label="请求超时(秒)">
                <el-input-number v-model="configForm.llm.request_timeout" :min="10" :max="600" :step="10" />
              </el-form-item>

              <el-form-item label="SSL 验证">
                <el-switch v-model="configForm.llm.ssl_verify" active-text="开启" inactive-text="关闭" />
                <span class="form-inline-help">正常应开启；仅企业内网/自签名证书时关闭</span>
              </el-form-item>
            </el-form>

            <!-- LLM Test Result -->
            <el-alert
              v-if="configTestResult"
              :title="configTestResult.success ? 'LLM 连接成功' : 'LLM 连接失败'"
              :type="configTestResult.success ? 'success' : 'error'"
              :description="configTestResult.message + (configTestResult.model_used ? '  |  模型: ' + configTestResult.model_used : '')"
              show-icon
              :closable="true"
              @close="configTestResult = null"
              style="margin-top: 16px"
            />
          </el-card>

          <!-- Data Source Configuration -->
          <el-card class="section-card" shadow="hover" style="margin-top: 16px">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">数据源配置</span>
              </div>
            </template>

            <el-form label-width="160px" style="max-width: 700px">
              <el-form-item label="数据源优先级">
                <el-input v-model="configForm.data_source.priority" placeholder="akshare,tushare,baostock,eastmoney,sina,tencent" />
                <div class="form-help">英文逗号分隔，按优先级排列。可选: akshare, tushare, baostock, eastmoney, sina, tencent</div>
              </el-form-item>

              <el-form-item label="请求超时(秒)">
                <el-input-number v-model="configForm.data_source.timeout" :min="1" :max="120" :step="1" />
              </el-form-item>

              <el-form-item label="Tushare Token">
                <el-input v-model="configForm.data_source.tushare_token" type="password" placeholder="留空则不修改" show-password />
                <div class="form-help">可选，在 tushare.pro 注册获取</div>
              </el-form-item>
            </el-form>
          </el-card>

          <!-- Display Timezone -->
          <el-card class="section-card" shadow="hover" style="margin-top: 16px">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">显示时区</span>
                <el-tooltip content="系统中所有日期时间的显示时区。修改后立即生效，影响所有页面的时间显示。" placement="top-end">
                  <el-icon style="color: #909399; cursor: help; font-size: 16px"><InfoFilled /></el-icon>
                </el-tooltip>
              </div>
            </template>

            <el-form label-width="160px" style="max-width: 700px">
              <el-form-item label="显示时区">
                <el-select v-model="configForm.display_timezone" filterable allow-create style="width: 100%"
                  placeholder="选择或输入时区">
                  <el-option label="Asia/Shanghai (北京/上海)" value="Asia/Shanghai" />
                  <el-option label="Asia/Hong_Kong (香港)" value="Asia/Hong_Kong" />
                  <el-option label="Asia/Taipei (台北)" value="Asia/Taipei" />
                  <el-option label="Asia/Tokyo (东京)" value="Asia/Tokyo" />
                  <el-option label="Asia/Singapore (新加坡)" value="Asia/Singapore" />
                  <el-option label="America/New_York (纽约)" value="America/New_York" />
                  <el-option label="America/Chicago (芝加哥)" value="America/Chicago" />
                  <el-option label="America/Los_Angeles (洛杉矶)" value="America/Los_Angeles" />
                  <el-option label="Europe/London (伦敦)" value="Europe/London" />
                  <el-option label="Europe/Berlin (柏林)" value="Europe/Berlin" />
                  <el-option label="UTC" value="UTC" />
                </el-select>
                <div class="form-help">所有页面的时间显示将使用此时区。A股交易时间为北京时间 9:30-15:00</div>
              </el-form-item>
            </el-form>
          </el-card>

          <!-- Service Ports -->
          <el-card class="section-card" shadow="hover" style="margin-top: 16px">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">服务端口配置</span>
              </div>
            </template>

            <el-alert
              title="端口修改需要重启对应服务才能生效"
              type="info"
              :closable="false"
              show-icon
              style="margin-bottom: 16px"
            />

            <el-form label-width="160px" style="max-width: 700px">
              <el-form-item label="后端 API 端口">
                <el-input-number v-model="configForm.service_ports.backend" :min="1" :max="65535" />
                <span class="form-inline-help">FastAPI / Uvicorn</span>
              </el-form-item>

              <el-form-item label="前端开发端口">
                <el-input-number v-model="configForm.service_ports.frontend" :min="1" :max="65535" />
                <span class="form-inline-help">Vite Dev Server</span>
              </el-form-item>

              <el-form-item label="PostgreSQL 端口">
                <el-input-number v-model="configForm.service_ports.postgres" :min="1" :max="65535" />
              </el-form-item>

              <el-form-item label="Redis 端口">
                <el-input-number v-model="configForm.service_ports.redis" :min="1" :max="65535" />
              </el-form-item>

              <el-form-item label="Grafana 端口">
                <el-input-number v-model="configForm.service_ports.grafana" :min="1" :max="65535" />
              </el-form-item>
            </el-form>
          </el-card>

          <!-- Broker (Live Trading) Configuration -->
          <el-card class="section-card" shadow="hover" style="margin-top: 16px">
            <template #header>
              <div class="card-header-row">
                <span class="card-title">实盘交易配置 (平安证券 QMT)</span>
                <el-button type="primary" :loading="configSaving" @click="saveConfigSettings">
                  保存配置
                </el-button>
              </div>
            </template>

            <el-alert
              title="实盘交易前置条件"
              type="warning"
              :closable="false"
              show-icon
              style="margin-bottom: 16px"
            >
              <template #default>
                <ol style="margin: 4px 0 0; padding-left: 20px; line-height: 1.8;">
                  <li>安装平安证券QMT客户端 (从平安证券官网下载)</li>
                  <li>安装 xtquant: <code>pip install xtquant</code> 或从QMT安装目录复制</li>
                  <li>启动 QMT Mini 客户端并登录账户</li>
                  <li>在下方填写账号和QMT路径后保存</li>
                </ol>
              </template>
            </el-alert>

            <el-form label-width="160px" style="max-width: 700px">
              <el-form-item label="资金账号">
                <el-input v-model="configForm.broker.account" placeholder="平安证券资金账号">
                  <template #prepend>BROKER_ACCOUNT</template>
                </el-input>
                <div class="form-help" v-if="configBrokerMasked">当前: {{ configBrokerMasked }}（留空则不修改）</div>
              </el-form-item>

              <el-form-item label="交易密码">
                <el-input
                  v-model="configForm.broker.password"
                  type="password"
                  placeholder="交易密码（留空则不修改）"
                  show-password
                >
                  <template #prepend>BROKER_PASSWORD</template>
                </el-input>
                <div class="form-help">密码保存在服务器 .env 文件中, 不会明文显示</div>
              </el-form-item>

              <el-form-item label="QMT 路径">
                <el-input v-model="configForm.broker.qmt_path" placeholder="C:/平安证券/QMT/bin.x64">
                  <template #prepend>BROKER_QMT_PATH</template>
                </el-input>
                <div class="form-help">QMT Mini 客户端的 bin.x64 目录路径</div>
              </el-form-item>

              <el-form-item label="xtquant 状态">
                <el-tag :type="configBrokerXtInstalled ? 'success' : 'danger'" size="small">
                  {{ configBrokerXtInstalled ? '已安装' : '未安装' }}
                </el-tag>
                <span v-if="!configBrokerXtInstalled" style="margin-left: 8px; font-size: 12px; color: #e6a23c;">
                  请安装 xtquant 后重启后端服务
                </span>
              </el-form-item>
            </el-form>
          </el-card>
        </div>

        <!-- ==================== Section 7: Strategy Trading ==================== -->
        <div v-if="activeMenu === 'strategy'">
          <div class="section-header">
            <h2>策略交易</h2>
            <el-button type="primary" @click="openCreateStrategy">新建策略</el-button>
          </div>

          <div style="display: flex; gap: 16px;">
            <!-- Strategy List (left panel) -->
            <el-card class="section-card" shadow="hover" style="flex: 1; min-width: 400px; max-width: 520px;">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">策略列表</span>
                  <el-button size="small" @click="loadStrategies" :loading="strategyLoading">
                    <el-icon><Refresh /></el-icon>
                  </el-button>
                </div>
              </template>
              <div v-if="strategies.length === 0" style="color: #999; text-align: center; padding: 40px 0;">
                暂无策略, 点击上方"新建策略"开始
              </div>
              <div v-for="s in strategies" :key="s.id"
                   :class="['strategy-item', { active: strategyDetailId === s.id }]"
                   @click="loadStrategyDetail(s.id)"
              >
                <div style="display: flex; justify-content: space-between; align-items: center;">
                  <div>
                    <strong>{{ s.name }}</strong>
                    <el-tag size="small" :type="s.mode === 'simulated' ? 'info' : 'danger'" style="margin-left: 6px">
                      {{ s.mode === 'simulated' ? '模拟' : '实盘' }}
                    </el-tag>
                  </div>
                  <el-tag size="small" :type="tradeStatusType(s.status)">{{ tradeStatusLabel(s.status) }}</el-tag>
                </div>
                <div style="color: #888; font-size: 12px; margin-top: 4px;">
                  <a class="stock-link" @click.stop="navigateToStock({ code: s.stock_code })">{{ s.stock_name }} ({{ s.stock_code }})</a> | {{ s.steps.length }}步骤
                </div>
                <div style="margin-top: 6px; display: flex; gap: 4px; flex-wrap: wrap;">
                  <el-button v-if="s.status === 'draft' || s.status === 'paused'" size="small" type="success" @click.stop="activateStrategy(s)">启动</el-button>
                  <el-button v-if="s.status === 'active'" size="small" type="warning" @click.stop="pauseStrategy(s)">暂停</el-button>
                  <el-button v-if="s.status !== 'completed' && s.status !== 'cancelled'" size="small" @click.stop="cancelStrategy(s)">取消</el-button>
                  <el-button v-if="s.status !== 'active'" size="small" @click.stop="openEditStrategy(s)">编辑</el-button>
                  <el-button v-if="s.status !== 'active' && s.status !== 'draft'" size="small" @click.stop="resetStrategy(s)">重置</el-button>
                  <el-button v-if="s.status !== 'active'" size="small" type="danger" @click.stop="deleteStrategy(s)">删除</el-button>
                </div>
              </div>
            </el-card>

            <!-- Strategy Detail (right panel) -->
            <div style="flex: 2; min-width: 0;" v-if="strategyDetail">
              <!-- Simulation Stats -->
              <el-card class="section-card" shadow="hover" style="margin-bottom: 16px;">
                <template #header>
                  <div class="card-header-row">
                    <span class="card-title">{{ strategyDetail.name }} — <a class="stock-link" @click="navigateToStock({ code: strategyDetail.stock_code })">{{ strategyDetail.stock_name }} ({{ strategyDetail.stock_code }})</a></span>
                    <el-tag :type="tradeStatusType(strategyDetail.status)">{{ tradeStatusLabel(strategyDetail.status) }}</el-tag>
                  </div>
                </template>
                <el-descriptions :column="4" border size="small">
                  <el-descriptions-item label="模式">
                    <el-tag size="small" :type="strategyDetail.mode === 'simulated' ? 'info' : 'danger'">
                      {{ strategyDetail.mode === 'simulated' ? '模拟盘' : '实盘' }}
                    </el-tag>
                  </el-descriptions-item>
                  <template v-if="strategyDetail.mode === 'simulated'">
                    <el-descriptions-item label="初始资金">{{ strategyDetail.sim_initial_cash.toLocaleString() }}</el-descriptions-item>
                    <el-descriptions-item label="可用资金">{{ strategyDetail.sim_cash.toLocaleString() }}</el-descriptions-item>
                    <el-descriptions-item label="持仓">{{ strategyDetail.sim_holdings }}股</el-descriptions-item>
                    <el-descriptions-item label="持仓均价">{{ strategyDetail.sim_avg_cost > 0 ? strategyDetail.sim_avg_cost.toFixed(2) : '-' }}</el-descriptions-item>
                  </template>
                  <template v-else>
                    <el-descriptions-item label="交易通道">平安证券 QMT</el-descriptions-item>
                    <el-descriptions-item label="说明">实盘资金由券商账户管理</el-descriptions-item>
                  </template>
                  <el-descriptions-item label="当前步骤">第{{ strategyDetail.current_step_order }}步 / 共{{ strategyDetail.steps.length }}步</el-descriptions-item>
                </el-descriptions>

                <!-- Test tick for simulated mode -->
                <div v-if="strategyDetail.status === 'active'" style="margin-top: 12px;">
                  <!-- Real-time monitoring controls -->
                  <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 10px; padding: 10px; background: #f0f9ff; border-radius: 6px; border: 1px solid #d0e8ff; flex-wrap: wrap;">
                    <el-icon v-if="autoTickTimer" color="#67c23a" class="pulse-icon"><Loading /></el-icon>
                    <span style="font-size: 13px; font-weight: 500; color: #333;">实时监控:</span>
                    <el-button v-if="!autoTickTimer" type="success" size="small" @click="startAutoTick" :loading="autoTickLoading">
                      开启实时行情
                    </el-button>
                    <el-button v-else type="danger" size="small" @click="stopAutoTick">
                      停止监控
                    </el-button>
                    <el-radio-group v-model="autoTickInterval" size="small" @change="onIntervalChange" style="margin-left: 4px;">
                      <el-radio-button :value="1000">1秒</el-radio-button>
                      <el-radio-button :value="3000">3秒</el-radio-button>
                      <el-radio-button :value="5000">5秒</el-radio-button>
                    </el-radio-group>
                    <!-- Live quote display -->
                    <div v-if="liveQuote" style="margin-left: auto; display: flex; gap: 12px; font-size: 13px;">
                      <span style="font-weight: 600;">{{ liveQuote.name }}</span>
                      <span :style="{ color: (liveQuote.change_pct || 0) >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 'bold' }">
                        {{ liveQuote.price?.toFixed(2) }}
                      </span>
                      <span :style="{ color: (liveQuote.change_pct || 0) >= 0 ? '#f56c6c' : '#67c23a' }">
                        {{ (liveQuote.change_pct || 0) >= 0 ? '+' : '' }}{{ liveQuote.change_pct?.toFixed(2) }}%
                      </span>
                      <span style="color: #999;">开:{{ liveQuote.open?.toFixed(2) || '-' }}</span>
                      <span style="color: #999;">昨收:{{ liveQuote.prev_close?.toFixed(2) || '-' }}</span>
                      <span style="color: #999;">量:{{ liveQuote.volume ? (liveQuote.volume / 10000).toFixed(0) + '万' : '-' }}</span>
                      <span style="color: #999;">换手:{{ liveQuote.turnover_rate?.toFixed(2) || '-' }}%</span>
                    </div>
                  </div>
                  <!-- Intraday Chart -->
                  <div v-if="autoTickTimer || intradayData.length > 0"
                       style="margin-bottom: 10px; border-radius: 6px; overflow: hidden;">
                    <div ref="intradayChartRef" style="width: 100%; height: 320px;"></div>
                  </div>
                  <!-- Manual test tick -->
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 13px; color: #666;">手动测试价格:</span>
                    <el-input-number v-model="testTickPrice" :precision="2" :step="0.01" :min="0" size="small" style="width: 150px" />
                    <el-button type="primary" size="small" @click="doTestTick" :loading="testTickLoading">注入行情</el-button>
                    <span style="font-size: 12px; color: #999;">模拟注入一个价格来测试条件触发</span>
                  </div>
                </div>
              </el-card>

              <!-- Steps Pipeline -->
              <el-card class="section-card" shadow="hover" style="margin-bottom: 16px;">
                <template #header>
                  <span class="card-title">执行步骤</span>
                </template>
                <div v-for="(step, idx) in strategyDetail.steps" :key="step.id"
                     :class="['step-card', step.status]">
                  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                    <div>
                      <strong>步骤{{ step.step_order }}:</strong> {{ step.name || (step.action_type === 'buy' ? '买入' : '卖出') }}
                      <el-tag size="small" :type="tradeStatusType(step.status)" style="margin-left: 6px">{{ tradeStatusLabel(step.status) }}</el-tag>
                    </div>
                    <div style="font-size: 13px; color: #666;">
                      <el-tag size="small" :type="step.action_type === 'buy' ? 'success' : 'danger'">
                        {{ step.action_type === 'buy' ? '买入' : '卖出' }}
                      </el-tag>
                      {{ step.quantity }}股
                      <span v-if="step.price_type === 'limit'">@ {{ step.limit_price }}</span>
                      <span v-else>@ 市价</span>
                    </div>
                  </div>
                  <!-- Conditions -->
                  <div style="padding-left: 16px; font-size: 13px;">
                    <div v-for="(cond, ci) in step.conditions" :key="cond.id" style="display: flex; align-items: center; gap: 6px; margin-bottom: 3px;">
                      <span v-if="ci > 0" style="color: #409eff; font-weight: bold;">{{ step.condition_logic }}</span>
                      <el-icon v-if="cond.is_met" color="#67c23a"><Select /></el-icon>
                      <el-icon v-else color="#ddd"><CloseBold /></el-icon>
                      <span>{{ fieldLabel(cond.field) }} {{ cond.operator }} {{ cond.value }}</span>
                      <span v-if="cond.is_met" style="color: #67c23a; font-size: 12px;">(已满足)</span>
                    </div>
                  </div>
                  <!-- Fill info -->
                  <div v-if="step.fill_price" style="margin-top: 6px; padding-left: 16px; color: #67c23a; font-size: 13px;">
                    已成交: {{ step.fill_quantity }}股 @ {{ step.fill_price.toFixed(2) }}
                    <span v-if="step.filled_at"> ({{ formatTz(step.filled_at) }})</span>
                  </div>
                  <!-- Arrow between steps -->
                  <div v-if="idx < strategyDetail.steps.length - 1" style="text-align: center; color: #409eff; font-size: 20px; margin: 4px 0;">
                    &#8595;
                  </div>
                </div>
              </el-card>

              <!-- Execution Log -->
              <el-card class="section-card" shadow="hover">
                <template #header>
                  <div class="card-header-row">
                    <span class="card-title">执行日志</span>
                    <el-button size="small" @click="loadStrategyExecs(strategyDetail.id)" :loading="strategyExecsLoading">
                      <el-icon><Refresh /></el-icon>
                    </el-button>
                  </div>
                </template>
                <el-table :data="strategyExecs" stripe border size="small" style="width: 100%" max-height="300">
                  <el-table-column prop="created_at" label="时间" width="170">
                    <template #default="{ row }">{{ formatTz(row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column prop="event_type" label="事件" width="130">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.event_type.includes('fail') || row.event_type === 'error' ? 'danger' : row.event_type.includes('filled') || row.event_type.includes('completed') ? 'success' : 'info'">
                        {{ row.event_type }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="message" label="详情" show-overflow-tooltip />
                  <el-table-column prop="price_snapshot" label="价格" width="90">
                    <template #default="{ row }">{{ row.price_snapshot ? row.price_snapshot.toFixed(2) : '-' }}</template>
                  </el-table-column>
                </el-table>
              </el-card>
            </div>

            <!-- No detail selected -->
            <div v-else style="flex: 2; display: flex; align-items: center; justify-content: center; color: #999;">
              <span>请从左侧选择一个策略查看详情</span>
            </div>
          </div>

          <!-- Create/Edit Strategy Dialog -->
          <el-dialog
            :title="editingStrategy ? '编辑策略' : '新建策略'"
            v-model="showStrategyDialog"
            width="750px"
            :close-on-click-modal="false"
          >
            <el-form label-width="100px" size="default">
              <el-form-item label="策略名称">
                <el-input v-model="strategyForm.name" placeholder="例如: 平安银行低吸高抛" />
              </el-form-item>
              <el-form-item label="股票">
                <el-select
                  v-model="strategyForm.stock_code"
                  filterable remote reserve-keyword
                  :remote-method="handleStrategySearch"
                  :loading="strategySearchLoading"
                  placeholder="输入代码或名称搜索"
                  style="width: 100%"
                  @change="selectStrategyStock(strategySearchResults.find(r => r.code === strategyForm.stock_code))"
                >
                  <el-option
                    v-for="item in strategySearchResults"
                    :key="item.code"
                    :label="`${item.code} ${item.name}`"
                    :value="item.code"
                  />
                </el-select>
                <span v-if="strategyForm.stock_name" style="margin-left: 8px; color: #666;">{{ strategyForm.stock_name }} ({{ strategyForm.market }})</span>
              </el-form-item>
              <el-form-item label="交易模式">
                <el-radio-group v-model="strategyForm.mode">
                  <el-radio value="simulated">模拟盘</el-radio>
                  <el-radio value="live">实盘 (平安证券)</el-radio>
                </el-radio-group>
                <div v-if="strategyForm.mode === 'live'" style="margin-top: 6px; font-size: 12px; color: #e6a23c;">
                  实盘模式将通过平安证券QMT接口进行真实交易, 请确认已在配置管理中设置券商账号
                </div>
              </el-form-item>
              <el-form-item label="初始资金" v-if="strategyForm.mode === 'simulated'">
                <el-input-number v-model="strategyForm.sim_initial_cash" :min="1000" :step="10000" />
              </el-form-item>
              <el-form-item label="备注">
                <el-input v-model="strategyForm.notes" type="textarea" :rows="2" />
              </el-form-item>
            </el-form>

            <!-- Steps Editor -->
            <div style="margin-top: 12px;">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="margin: 0;">步骤编排 (按顺序执行)</h3>
                <el-button type="primary" size="small" @click="addStep">添加步骤</el-button>
              </div>

              <div v-if="strategyForm.steps.length === 0" style="color: #999; text-align: center; padding: 20px; background: #f5f7fa; border-radius: 6px;">
                点击"添加步骤"来创建条件单。步骤按顺序执行: 步骤1完成后自动激活步骤2, 以此类推。
              </div>

              <div v-for="(step, si) in strategyForm.steps" :key="si"
                   style="border: 1px solid #e4e7ed; border-radius: 6px; padding: 16px; margin-bottom: 12px; background: #fafafa;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <el-tag type="info" size="small">步骤{{ si + 1 }}</el-tag>
                    <el-input v-model="step.name" placeholder="步骤名称 (可选)" size="small" style="width: 180px" />
                  </div>
                  <el-button type="danger" size="small" @click="removeStep(si)">删除步骤</el-button>
                </div>

                <!-- Action -->
                <div style="display: flex; gap: 12px; align-items: center; margin-bottom: 10px;">
                  <span style="font-size: 13px; color: #666; white-space: nowrap;">触发后执行:</span>
                  <el-select v-model="step.action_type" size="small" style="width: 90px">
                    <el-option value="buy" label="买入" />
                    <el-option value="sell" label="卖出" />
                  </el-select>
                  <el-input-number v-model="step.quantity" :min="1" :step="100" size="small" style="width: 130px" />
                  <span style="font-size: 13px; color: #666;">股 @</span>
                  <el-select v-model="step.price_type" size="small" style="width: 90px">
                    <el-option value="market" label="市价" />
                    <el-option value="limit" label="限价" />
                  </el-select>
                  <el-input-number v-if="step.price_type === 'limit'" v-model="step.limit_price" :precision="2" :step="0.01" :min="0" size="small" style="width: 130px" placeholder="限价" />
                </div>

                <!-- Conditions -->
                <div style="border-left: 3px solid #409eff; padding-left: 12px; margin-top: 8px;">
                  <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                    <span style="font-size: 13px; color: #666;">触发条件:</span>
                    <el-radio-group v-model="step.condition_logic" size="small">
                      <el-radio-button value="AND">全部满足 (AND)</el-radio-button>
                      <el-radio-button value="OR">任一满足 (OR)</el-radio-button>
                    </el-radio-group>
                    <el-button type="primary" size="small" link @click="addCondition(step)">+ 添加条件</el-button>
                  </div>
                  <div v-for="(cond, ci) in step.conditions" :key="ci"
                       style="display: flex; align-items: center; gap: 6px; margin-bottom: 6px;">
                    <el-select v-model="cond.field" size="small" style="width: 155px">
                      <el-option v-for="f in CONDITION_FIELDS" :key="f.value" :value="f.value" :label="f.label" />
                    </el-select>
                    <el-select v-model="cond.operator" size="small" style="width: 70px">
                      <el-option v-for="o in CONDITION_OPS" :key="o" :value="o" :label="o" />
                    </el-select>
                    <el-input-number v-model="cond.value" :precision="4" :step="0.01" size="small" style="width: 140px" />
                    <el-button type="danger" size="small" link @click="removeCondition(step, ci)" v-if="step.conditions.length > 1">删除</el-button>
                  </div>
                </div>

                <!-- Arrow between steps -->
                <div v-if="si < strategyForm.steps.length - 1" style="text-align: center; color: #409eff; font-size: 18px; margin-top: 8px;">
                  &#8595; 成交后进入下一步
                </div>
              </div>
            </div>

            <template #footer>
              <el-button @click="showStrategyDialog = false">取消</el-button>
              <el-button type="primary" @click="saveStrategy">{{ editingStrategy ? '保存修改' : '创建策略' }}</el-button>
            </template>
          </el-dialog>
        </div>

        <!-- ==================== Section 8: Cross-Sectional Quantitative Analysis ==================== -->
        <div v-if="activeMenu === 'quant'">
          <div class="section-header">
            <h2>量化选股</h2>
            <el-radio-group v-model="quantTab" size="small" @change="v => { pushMenuState('quant', { quantTab: v }); if (v === 'history') loadQuantHistory() }">
              <el-radio-button value="new">分析</el-radio-button>
              <el-radio-button value="history">历史记录</el-radio-button>
            </el-radio-group>
          </div>

          <!-- ===== Tab: New Analysis ===== -->
          <template v-if="quantTab === 'new'">

          <!-- Viewing indicator -->
          <el-alert v-if="quantViewingRunId && quantResult" type="info" :closable="false" style="margin-bottom: 12px;">
            <template #title>
              <span>查看历史结果: {{ quantResult.name || quantResult.run_id }}</span>
              <el-button type="primary" link size="small" style="margin-left: 12px;" @click="quantResult = null; quantViewingRunId = null;">返回新建分析</el-button>
            </template>
          </el-alert>

          <!-- Config & Run -->
          <el-card class="section-card" shadow="hover" v-if="!quantViewingRunId">
            <!-- Row 1: Preset + quick config -->
            <el-row :gutter="16" align="middle" style="margin-bottom: 12px;">
              <el-col :span="6">
                <div style="font-size: 12px; color: #999; margin-bottom: 4px;">选股宇宙预设</div>
                <el-select v-model="quantSelectedPreset" placeholder="选择预设..." size="small" clearable style="width: 100%;" @change="onPresetChange">
                  <el-option v-for="p in quantPresets" :key="p.name" :label="p.label" :value="p.name">
                    <span>{{ p.label }}</span>
                    <span style="float: right; color: #999; font-size: 11px; max-width: 180px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">{{ p.description }}</span>
                  </el-option>
                  <el-option label="自定义筛选" value="custom" />
                </el-select>
              </el-col>
              <el-col :span="3">
                <div style="font-size: 12px; color: #999; margin-bottom: 4px;">组合大小</div>
                <el-input-number v-model="quantTopN" :min="10" :max="100" :step="10" size="small" style="width: 100%;" />
              </el-col>
              <el-col :span="3">
                <div style="font-size: 12px; color: #999; margin-bottom: 4px;">换仓周期(天)</div>
                <el-input-number v-model="quantRebalanceFreq" :min="5" :max="30" :step="5" size="small" style="width: 100%;" />
              </el-col>
              <el-col :span="3">
                <div style="font-size: 12px; color: #999; margin-bottom: 4px;">行业中性化</div>
                <el-switch v-model="quantIndustryNeutral" size="small" />
              </el-col>
              <el-col :span="4">
                <el-button type="primary" @click="runQuantAnalysis" :loading="quantLoading" :disabled="quantLoading" style="margin-top: 18px;">
                  <el-icon><Histogram /></el-icon> 开始分析
                </el-button>
              </el-col>
              <el-col :span="5">
                <span style="color: #999; font-size: 11px;">
                  50因子 × 8类别 + IC加权 + ML增强 + 回测<br/>
                  <el-link type="primary" :underline="false" style="font-size: 11px;" @click="quantShowAdvancedFilters = !quantShowAdvancedFilters">
                    {{ quantShowAdvancedFilters ? '收起自定义筛选' : '展开自定义筛选' }}
                  </el-link>
                </span>
              </el-col>
            </el-row>
            <!-- Row 2: Advanced filters (collapsible) -->
            <el-collapse-transition>
              <div v-show="quantShowAdvancedFilters || quantSelectedPreset === 'custom'" style="padding-top: 8px; border-top: 1px solid #f0f0f0;">
                <el-row :gutter="12" align="top" style="margin-bottom: 8px;">
                  <el-col :span="8">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">行业筛选 <span style="font-size: 11px;">(点击标签切换+/-)</span></div>
                    <el-select v-model="quantIndustryTemp" placeholder="搜索添加行业..." size="small" filterable clearable style="width: 100%;" @change="addQuantIndustry">
                      <el-option v-for="ind in quantIndustryAvailable" :key="ind" :label="ind" :value="ind" />
                    </el-select>
                    <div v-if="quantSelectedIndustries.length || quantExcludedIndustries.length" style="margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px;">
                      <el-tag v-for="ind in quantSelectedIndustries" :key="'+'+ind" type="success" closable size="small" @click="toggleQuantIndustry(ind)" @close="removeQuantIndustry(ind)" style="cursor: pointer;">+{{ ind }}</el-tag>
                      <el-tag v-for="ind in quantExcludedIndustries" :key="'-'+ind" type="danger" closable size="small" @click="toggleQuantIndustry(ind)" @close="removeQuantIndustry(ind)" style="cursor: pointer;">-{{ ind }}</el-tag>
                    </div>
                  </el-col>
                  <el-col :span="4">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">市值下限(亿)</div>
                    <el-input-number v-model="quantMarketCapMin" :min="0" :step="10" :precision="0" size="small" style="width: 100%;" placeholder="不限" controls-position="right" />
                  </el-col>
                  <el-col :span="4">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">市值上限(亿)</div>
                    <el-input-number v-model="quantMarketCapMax" :min="0" :step="100" :precision="0" size="small" style="width: 100%;" placeholder="不限" controls-position="right" />
                  </el-col>
                  <el-col :span="2">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">PE下限</div>
                    <el-input-number v-model="quantPeMin" :step="5" :precision="1" size="small" style="width: 100%;" controls-position="right" />
                  </el-col>
                  <el-col :span="2">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">PE上限</div>
                    <el-input-number v-model="quantPeMax" :step="5" :precision="1" size="small" style="width: 100%;" controls-position="right" />
                  </el-col>
                  <el-col :span="2">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">PB下限</div>
                    <el-input-number v-model="quantPbMin" :step="0.5" :precision="1" size="small" style="width: 100%;" controls-position="right" />
                  </el-col>
                  <el-col :span="2">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">PB上限</div>
                    <el-input-number v-model="quantPbMax" :step="0.5" :precision="1" size="small" style="width: 100%;" controls-position="right" />
                  </el-col>
                </el-row>
                <el-row :gutter="12" align="middle" style="margin-top: 16px;">
                  <el-col :span="6">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">
                      回测/训练周期
                      <el-tooltip content="选择历史数据的时间范围，用于因子IC计算、ML模型训练/测试、以及回测评估。周期越长统计显著性越高，但可能包含不同市场状态；周期越短越贴近当前市场。建议使用2-3年。" placement="top">
                        <el-icon style="color: #909399; cursor: help; margin-left: 4px;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                    <el-select v-model="quantBacktestPreset" size="small" style="width: 100%;" @change="onBacktestPresetChange">
                      <el-option v-for="p in BACKTEST_RANGE_PRESETS" :key="p.value" :label="p.label" :value="p.value">
                        <span>{{ p.label }}</span>
                        <span style="float: right; color: #999; font-size: 11px;">{{ p.desc }}</span>
                      </el-option>
                    </el-select>
                  </el-col>
                  <el-col :span="4" v-if="quantBacktestPreset === 'custom'">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">开始日期</div>
                    <el-date-picker v-model="quantBacktestStart" type="date" value-format="YYYY-MM-DD" placeholder="开始日期" size="small" style="width: 100%;" />
                  </el-col>
                  <el-col :span="4" v-if="quantBacktestPreset === 'custom'">
                    <div style="font-size: 12px; color: #999; margin-bottom: 4px;">结束日期</div>
                    <el-date-picker v-model="quantBacktestEnd" type="date" value-format="YYYY-MM-DD" placeholder="结束日期 (默认今天)" size="small" style="width: 100%;" />
                  </el-col>
                  <el-col :span="14" v-if="quantBacktestPreset !== 'custom'">
                    <div style="font-size: 11px; color: #bbb; margin-top: 18px;">
                      IC因子检验使用全区间数据; ML按时间80/20切分(前80%训练, 后20%测试); 回测使用全区间
                    </div>
                  </el-col>
                </el-row>
              </div>
            </el-collapse-transition>
          </el-card>

          <!-- Error display -->
          <el-alert v-if="quantError" :title="quantError" type="error" show-icon closable style="margin-bottom: 16px;" @close="quantError = ''" />

          <!-- Loading with poll status -->
          <div v-if="quantLoading" style="text-align: center; padding: 80px 0;">
            <el-icon class="is-loading" :size="40" color="#409eff"><Loading /></el-icon>
            <p style="color: #666; margin-top: 12px;">{{ quantTaskMsg || '正在进行横截面因子分析...' }}</p>
            <p style="color: #999; font-size: 12px;">预筛选 → 加载K线 → 过滤宇宙 → 计算50因子 → 横截面排名 → IC检验 → ML增强 → 组合构建 → 回测</p>
            <p style="color: #bbb; font-size: 11px; margin-top: 8px;">结果将自动保存到历史记录</p>
          </div>

          <!-- Results -->
          <div v-if="quantResult && !quantLoading">

            <!-- Analysis Parameters (filters_applied + config) -->
            <el-card class="section-card" shadow="hover" v-if="quantResult.filters_applied || quantResult.portfolio?.config" style="margin-bottom: 16px;">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">分析参数</span>
                  <el-tooltip placement="top-end">
                    <template #content>
                      <div style="max-width: 360px; line-height: 1.6;">
                        本次量化分析使用的筛选条件和组合配置参数。<br/><br/>
                        <b>策略预设</b>: 预定义的股票池筛选规则 (如沪深300风格、价值股等)。<br/>
                        <b>Top-N</b>: 最终组合持有的股票数量。<br/>
                        <b>换仓周期</b>: 每隔多少个交易日重新排名并调仓。<br/>
                        <b>行业中性</b>: 开启后组合会按行业均衡配置，避免过度集中。<br/>
                        <b>市值/PE/PB</b>: 预筛选条件，在因子计算前先缩小股票池。
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <div style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center;">
                <el-tag v-if="quantResult.filters_applied?.preset" type="primary" size="small" effect="dark">
                  {{ (quantPresets.find(p => p.name === quantResult.filters_applied.preset) || {}).label || quantResult.filters_applied.preset }}
                </el-tag>
                <el-tag v-if="quantResult.portfolio?.config?.top_n" size="small">
                  Top-{{ quantResult.portfolio.config.top_n }}
                </el-tag>
                <el-tag v-if="quantResult.portfolio?.config?.rebalance_freq" size="small">
                  换仓 {{ quantResult.portfolio.config.rebalance_freq }}天
                </el-tag>
                <el-tag v-if="quantResult.portfolio?.config?.industry_neutral" type="warning" size="small">
                  行业中性
                </el-tag>
                <el-tag v-if="quantResult.filters_applied?.market_cap_min || quantResult.filters_applied?.market_cap_max" size="small" type="info">
                  市值: {{ quantResult.filters_applied.market_cap_min ? quantResult.filters_applied.market_cap_min + '亿' : '不限' }}
                  ~ {{ quantResult.filters_applied.market_cap_max ? quantResult.filters_applied.market_cap_max + '亿' : '不限' }}
                </el-tag>
                <el-tag v-if="quantResult.filters_applied?.pe_min != null || quantResult.filters_applied?.pe_max != null" size="small" type="info">
                  PE: {{ quantResult.filters_applied.pe_min != null ? quantResult.filters_applied.pe_min : '不限' }}
                  ~ {{ quantResult.filters_applied.pe_max != null ? quantResult.filters_applied.pe_max : '不限' }}
                </el-tag>
                <el-tag v-if="quantResult.filters_applied?.pb_min != null || quantResult.filters_applied?.pb_max != null" size="small" type="info">
                  PB: {{ quantResult.filters_applied.pb_min != null ? quantResult.filters_applied.pb_min : '不限' }}
                  ~ {{ quantResult.filters_applied.pb_max != null ? quantResult.filters_applied.pb_max : '不限' }}
                </el-tag>
                <el-tag v-if="quantResult.filters_applied?.industries && quantResult.filters_applied.industries.length" size="small" type="success">
                  行业: {{ Array.isArray(quantResult.filters_applied.industries) ? quantResult.filters_applied.industries.join(', ') : quantResult.filters_applied.industries }}
                </el-tag>
                <el-tag v-if="quantResult.filters_applied?.markets" size="small" type="info">
                  市场: {{ quantResult.filters_applied.markets }}
                </el-tag>
                <span v-if="quantResult.filters_applied?.backtest_start || quantResult.filters_applied?.backtest_end" style="font-size: 12px; color: #999;">
                  回测: {{ quantResult.filters_applied.backtest_start || '-' }} ~ {{ quantResult.filters_applied.backtest_end || '今' }}
                </span>
              </div>
            </el-card>

            <!-- Universe & Signal Overview -->
            <el-row :gutter="16" style="margin-bottom: 16px;">
              <el-col :span="6">
                <el-card shadow="hover" class="section-card">
                  <div class="quant-metric-box">
                    <div class="quant-metric-label">
                      交易宇宙
                      <el-tooltip content="交易宇宙是经过预筛选（行业、市值、PE、PB等）和质量过滤（排除ST、次新股、涨跌停、低流动性、北交所）后，实际参与因子计算和排名的股票数量。" placement="top">
                        <el-icon style="color: #c0c4cc; cursor: help; margin-left: 4px; vertical-align: middle;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                    <div class="quant-metric-value" style="color: #409eff;">{{ quantResult.universe?.in_universe }}</div>
                    <div style="font-size: 11px; color: #999; margin-top: 4px;">/ {{ quantResult.universe?.total_stocks }} 总股票 (排除 {{ quantResult.universe?.excluded }})</div>
                  </div>
                </el-card>
              </el-col>
              <el-col :span="6">
                <el-card shadow="hover" class="section-card">
                  <div class="quant-metric-box">
                    <div class="quant-metric-label">
                      有效因子
                      <el-tooltip content="有效因子是同时满足三个条件的因子: |IC均值|>=0.03（有预测信号）、|IC_IR|>=0.3（信号稳定）、方向一致性>=55%。只有有效因子会被纳入组合信号计算，无效因子被排除。" placement="top">
                        <el-icon style="color: #c0c4cc; cursor: help; margin-left: 4px; vertical-align: middle;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                    <div class="quant-metric-value" style="color: #67c23a;">{{ quantResult.signal?.valid_factors }}</div>
                    <div style="font-size: 11px; color: #999; margin-top: 4px;">/ {{ quantResult.signal?.total_factors }} 总因子 (IC_IR >= 0.3)</div>
                  </div>
                </el-card>
              </el-col>
              <el-col :span="6">
                <el-card shadow="hover" class="section-card">
                  <div class="quant-metric-box">
                    <div class="quant-metric-label">
                      ML模型
                      <el-tooltip content="使用LightGBM机器学习模型对因子进行非线性增强。模型在前80%时间段训练，后20%测试。准确率表示模型在测试集上预测涨跌方向的正确率。ML预测结果会与IC加权信号按比例混合，生成最终综合评分。" placement="top">
                        <el-icon style="color: #c0c4cc; cursor: help; margin-left: 4px; vertical-align: middle;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                    <div class="quant-metric-value" :style="{ color: quantResult.ml_model?.available ? '#67c23a' : '#999' }">
                      {{ quantResult.ml_model?.available ? (quantResult.ml_model.accuracy * 100).toFixed(1) + '%' : 'N/A' }}
                    </div>
                    <div style="font-size: 11px; color: #999; margin-top: 4px;">{{ quantResult.ml_model?.model_type || '未启用' }} (权重 {{ (quantResult.ml_model?.weight_ratio * 100) || 0 }}%)</div>
                  </div>
                </el-card>
              </el-col>
              <el-col :span="6">
                <el-card shadow="hover" class="section-card">
                  <div class="quant-metric-box">
                    <div class="quant-metric-label">
                      分析耗时
                      <el-tooltip content="完成整个分析流程的总耗时，包括: 数据加载、宇宙过滤、50个因子计算、横截面排名、IC检验、ML模型训练与预测、组合构建、历史回测。" placement="top">
                        <el-icon style="color: #c0c4cc; cursor: help; margin-left: 4px; vertical-align: middle;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                    <div class="quant-metric-value" style="color: #909399;">{{ quantResult.elapsed_seconds }}s</div>
                    <div style="font-size: 11px; color: #999; margin-top: 4px;">分析日期: {{ quantResult.analysis_date }}</div>
                  </div>
                </el-card>
              </el-col>
            </el-row>

            <!-- Universe Filter Breakdown -->
            <el-card class="section-card" shadow="hover" v-if="quantResult.universe?.exclude_breakdown">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">宇宙过滤明细</span>
                  <el-tooltip placement="top-end">
                    <template #content>
                      <div style="max-width: 360px; line-height: 1.6;">
                        从预筛选股票池中剔除不适合量化分析的股票:<br/><br/>
                        <b>ST</b>: 被特别处理的问题股票，基本面恶化。<br/>
                        <b>IPO&lt;60天</b>: 上市不满60天的次新股，价格波动异常。<br/>
                        <b>涨跌停</b>: 当日涨跌幅超9.8%的股票，无法正常买卖。<br/>
                        <b>流动性不足</b>: 日均成交额低于1000万，交易成本高。<br/>
                        <b>北交所</b>: 北交所股票流动性较差，暂不纳入。
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <el-row :gutter="8">
                <el-col :span="4" v-for="(count, reason) in quantResult.universe.exclude_breakdown" :key="reason">
                  <el-tag type="info" size="small" style="margin: 2px;">{{ reason }}: {{ count }}</el-tag>
                </el-col>
              </el-row>
            </el-card>

            <!-- Data Splits -->
            <el-card class="section-card" shadow="hover" v-if="quantResult.data_splits">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">数据集划分</span>
                  <el-tooltip placement="top">
                    <template #content>
                      <div style="max-width: 360px; line-height: 1.6;">
                        <b>IC因子检验</b>: 在整个数据周期内，对每个交易日计算因子值与未来10日收益的横截面Spearman相关系数，评估因子预测能力。<br/>
                        <b>ML训练集</b>: 前80%交易日的数据用于训练LightGBM分类模型。<br/>
                        <b>ML测试集</b>: 后20%交易日用于评估模型泛化能力（防止过拟合）。<br/>
                        <b>回测区间</b>: 使用选定的整个周期进行模拟交易回测，评估策略实盘表现。
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <el-row :gutter="16">
                <el-col :span="6">
                  <div class="quant-stat-item">
                    <span class="quant-stat-label">数据总范围</span>
                    <span class="quant-stat-value" style="font-size: 13px;">
                      {{ quantResult.data_splits.data_date_range?.[0] || '-' }}
                    </span>
                    <span style="font-size: 11px; color: #999;">至 {{ quantResult.data_splits.data_date_range?.[1] || '-' }} ({{ quantResult.data_splits.total_dates }}天)</span>
                  </div>
                </el-col>
                <el-col :span="6">
                  <div class="quant-stat-item">
                    <span class="quant-stat-label">
                      ML训练集 ({{ Math.round((quantResult.data_splits.ml_train_ratio || 0.8) * 100) }}%)
                    </span>
                    <span class="quant-stat-value" style="font-size: 13px; color: #409eff;">
                      {{ quantResult.data_splits.ml_train_date_range?.[0] || '-' }}
                    </span>
                    <span style="font-size: 11px; color: #999;">至 {{ quantResult.data_splits.ml_train_date_range?.[1] || '-' }} ({{ quantResult.data_splits.ml_train_samples?.toLocaleString() }}条)</span>
                  </div>
                </el-col>
                <el-col :span="6">
                  <div class="quant-stat-item">
                    <span class="quant-stat-label">
                      ML测试集 ({{ 100 - Math.round((quantResult.data_splits.ml_train_ratio || 0.8) * 100) }}%)
                    </span>
                    <span class="quant-stat-value" style="font-size: 13px; color: #e6a23c;">
                      {{ quantResult.data_splits.ml_test_date_range?.[0] || '-' }}
                    </span>
                    <span style="font-size: 11px; color: #999;">至 {{ quantResult.data_splits.ml_test_date_range?.[1] || '-' }} ({{ quantResult.data_splits.ml_test_samples?.toLocaleString() }}条)</span>
                  </div>
                </el-col>
                <el-col :span="6">
                  <div class="quant-stat-item">
                    <span class="quant-stat-label">回测区间</span>
                    <span class="quant-stat-value" style="font-size: 13px; color: #67c23a;">
                      {{ quantResult.data_splits.backtest_date_range?.[0] || '-' }}
                    </span>
                    <span style="font-size: 11px; color: #999;">至 {{ quantResult.data_splits.backtest_date_range?.[1] || '-' }} ({{ quantResult.data_splits.backtest_days }}天)</span>
                  </div>
                </el-col>
              </el-row>
            </el-card>

            <!-- Top-N Portfolio -->
            <el-card class="section-card" shadow="hover">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">Top {{ quantResult.portfolio?.size }} 推荐组合</span>
                  <div style="display: flex; align-items: center; gap: 8px;">
                    <span style="font-size: 12px; color: #999;">
                      换仓周期: {{ quantResult.portfolio?.config?.rebalance_freq }}天 |
                      行业中性: {{ quantResult.portfolio?.config?.industry_neutral ? '开启' : '关闭' }}
                    </span>
                    <el-tooltip placement="top-end">
                      <template #content>
                        <div style="max-width: 380px; line-height: 1.8;">
                          <b>如何使用推荐组合:</b><br/>
                          1. 下方表格是基于<b>最新数据</b>计算的因子综合评分排名，即<b>当前应持有</b>的股票。<br/>
                          2. 如果您决定采用此策略，<b>立即等权买入</b>下方全部股票（每只分配 1/{{ quantResult.portfolio?.size }} 仓位）。<br/>
                          3. 每隔 <b>{{ quantResult.portfolio?.config?.rebalance_freq }} 个交易日</b>重新运行一次量化分析，按新的排名<b>调仓换股</b>。<br/>
                          4. 新组合中不再出现的股票卖出，新进入的股票买入。<br/><br/>
                          <b>回测绩效</b>验证的是：如果过去{{ quantResult.data_splits?.backtest_days || '' }}个交易日一直这样操作，历史收益如何。它是对<b>策略本身</b>的检验，不是对当前这批股票的预测。
                        </div>
                      </template>
                      <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                    </el-tooltip>
                  </div>
                </div>
              </template>

              <!-- Iteration portfolio selector (only shown when iterations exist) -->
              <div v-if="quantResultIterations.length > 0" style="margin-bottom: 12px;">
                <el-alert type="success" :closable="false" show-icon style="margin-bottom: 10px;">
                  <template #title>
                    <span style="font-size: 12px;">
                      此策略已开启自动迭代，共 <b>{{ quantResult.total_iterations }}</b> 期。
                      每期均产生新的推荐组合，可通过下方选择器查看各期持仓。
                    </span>
                  </template>
                </el-alert>
                <div style="display: flex; align-items: center; gap: 12px; flex-wrap: wrap;">
                  <span style="font-size: 13px; font-weight: 600; white-space: nowrap;">切换期数:</span>
                  <el-select v-model="quantResultIterViewNum" size="small" style="width: 400px;" placeholder="选择迭代期数查看组合">
                    <el-option
                      v-for="it in quantResultIterations"
                      :key="it.iteration_num"
                      :value="it.iteration_num"
                      :label="it.iteration_num === 0
                        ? `第0期 首次持仓 (${it.start_date}${it.end_date ? ' ~ ' + it.end_date : ''}) - ${it.portfolio_size}只`
                        : `第${it.iteration_num}期 (${it.start_date}${it.end_date ? ' ~ ' + it.end_date : ' 进行中'}) - ${it.portfolio_size}只`"
                    />
                  </el-select>
                  <el-tag v-if="quantResultIterViewItem" :type="quantResultIterViewItem.status === 'active' ? 'warning' : quantResultIterViewItem.iteration_num === 0 ? 'primary' : 'success'" size="small">
                    {{ quantResultIterViewItem.status === 'active' ? '当前持仓' : quantResultIterViewItem.iteration_num === 0 ? '首次持仓' : '已换仓' }}
                  </el-tag>
                  <span v-if="quantResultIterViewItem && quantResultIterViewItem.period_return_pct != null" style="font-size: 12px;">
                    期间收益:
                    <b :style="{ color: quantResultIterViewItem.period_return_pct >= 0 ? '#f56c6c' : '#67c23a' }">
                      {{ quantResultIterViewItem.period_return_pct > 0 ? '+' : '' }}{{ quantResultIterViewItem.period_return_pct }}%
                    </b>
                  </span>
                  <span v-if="quantResultIterViewItem && quantResultIterViewItem.nav != null" style="font-size: 12px;">
                    NAV: <b>{{ quantResultIterViewItem.nav.toFixed(4) }}</b>
                  </span>
                </div>
                <!-- Buy/Sell changes for this iteration -->
                <div v-if="quantResultIterViewItem && (quantResultIterViewItem.new_buys?.length || quantResultIterViewItem.new_sells?.length)" style="margin-top: 6px; font-size: 12px; line-height: 1.8;">
                  <span v-if="quantResultIterViewItem.new_buys?.length" style="color: #f56c6c;">
                    买入({{ quantResultIterViewItem.new_buys.length }})
                  </span>
                  <span v-if="quantResultIterViewItem.new_buys?.length && quantResultIterViewItem.new_sells?.length" style="margin: 0 4px; color: #ddd;">|</span>
                  <span v-if="quantResultIterViewItem.new_sells?.length" style="color: #67c23a;">
                    卖出({{ quantResultIterViewItem.new_sells.length }})
                  </span>
                </div>
              </div>

              <el-alert
                v-if="quantResultIterations.length === 0"
                type="info"
                :closable="false"
                show-icon
                style="margin-bottom: 12px;"
              >
                <template #title>
                  <span style="font-size: 12px;">
                    <b>操作指引:</b>
                    以下{{ quantResult.portfolio?.size }}只股票是基于{{ quantResult.analysis_date || '最新' }}数据的推荐持仓。
                    采用此策略请<b>等权买入</b>全部股票，并每<b>{{ quantResult.portfolio?.config?.rebalance_freq }}个交易日</b>重新分析、调仓换股。
                    下方「回测绩效」为该策略在
                    {{ quantResult.data_splits?.backtest_date_range?.[0] || '' }} 至 {{ quantResult.data_splits?.backtest_date_range?.[1] || '' }}
                    期间的历史模拟表现。
                  </span>
                </template>
              </el-alert>
              <el-table :data="quantResultDisplayPortfolio" size="small" stripe max-height="500" style="width: 100%;">
                <el-table-column type="index" label="#" width="50" align="center" />
                <el-table-column prop="stock_code" label="代码" width="80">
                  <template #default="{ row }">
                    <a class="stock-link" style="font-family: monospace; font-weight: 600;" @click="navigateToStock({ code: row.stock_code })">{{ row.stock_code }}</a>
                  </template>
                </el-table-column>
                <el-table-column prop="stock_name" label="名称" width="100">
                  <template #default="{ row }">
                    <a class="stock-link" @click="navigateToStock({ code: row.stock_code })">{{ row.stock_name }}</a>
                  </template>
                </el-table-column>
                <el-table-column prop="industry" label="行业" width="100">
                  <template #default="{ row }">
                    <el-tag size="small" type="info">{{ row.industry || '-' }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="score" label="综合分" width="80" align="right" sortable>
                  <template #default="{ row }">
                    <span :style="{ color: (row.score || 0) >= 70 ? '#f56c6c' : (row.score || 0) >= 50 ? '#e6a23c' : '#67c23a', fontWeight: 600 }">
                      {{ row.score?.toFixed?.(1) ?? row.score ?? '-' }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column v-if="quantResultIterViewItem" label="变动" width="70" align="center">
                  <template #default="{ row }">
                    <el-tag v-if="quantResultIterViewItem?.new_buys?.includes(row.stock_code)" type="danger" size="small">新买入</el-tag>
                    <span v-else style="color: #999; font-size: 11px;">持有</span>
                  </template>
                </el-table-column>
                <el-table-column v-if="quantResultIterViewItem?.stock_returns?.length" label="期间涨跌幅" width="100" align="right" sortable :sort-method="(a, b) => { const ra = quantResultIterViewItem.stock_returns.find(s => s.code === a.stock_code); const rb = quantResultIterViewItem.stock_returns.find(s => s.code === b.stock_code); return (ra?.return_pct ?? 0) - (rb?.return_pct ?? 0) }">
                  <template #default="{ row }">
                    <template v-if="quantResultIterViewItem.stock_returns">
                      <span v-for="sr in quantResultIterViewItem.stock_returns.filter(s => s.code === row.stock_code)" :key="sr.code"
                        :style="{ color: (sr.return_pct || 0) >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                        {{ sr.return_pct != null ? ((sr.return_pct > 0 ? '+' : '') + sr.return_pct + '%') : '-' }}
                      </span>
                    </template>
                  </template>
                </el-table-column>
                <el-table-column prop="pe_ttm" label="PE" width="70" align="right">
                  <template #default="{ row }">{{ row.pe_ttm ? row.pe_ttm.toFixed(1) : '-' }}</template>
                </el-table-column>
                <el-table-column prop="pb" label="PB" width="60" align="right">
                  <template #default="{ row }">{{ row.pb ? row.pb.toFixed(2) : '-' }}</template>
                </el-table-column>
                <el-table-column prop="total_market_cap" label="市值(亿)" width="90" align="right">
                  <template #default="{ row }">{{ formatMcap(row.total_market_cap) }}</template>
                </el-table-column>
                <el-table-column prop="market" label="市场" width="60" align="center" />
              </el-table>
            </el-card>

            <!-- Backtest Performance + Radar -->
            <el-row :gutter="16">
              <el-col :span="16">
                <el-card class="section-card" shadow="hover">
                  <template #header>
                    <div class="card-header-row">
                      <span class="card-title">回测绩效</span>
                      <span v-if="quantResult.data_splits?.backtest_date_range" style="font-size: 12px; color: #999;">
                        {{ quantResult.data_splits.backtest_date_range[0] }} 至 {{ quantResult.data_splits.backtest_date_range[1] }}
                        ({{ quantResult.data_splits.backtest_days }}个交易日)
                      </span>
                      <el-tooltip placement="top-end">
                        <template #content>
                          <div style="max-width: 380px; line-height: 1.8;">
                            <b>什么是回测?</b><br/>
                            回测是对量化策略的<b>历史模拟验证</b>。假设从{{ quantResult.data_splits?.backtest_date_range?.[0] || '起始日' }}开始，每{{ quantResult.portfolio?.config?.rebalance_freq || 10 }}个交易日按因子排名选股并换仓，一直执行到{{ quantResult.data_splits?.backtest_date_range?.[1] || '结束日' }}，累计收益和风险指标如下。<br/><br/>
                            <b>总收益</b> = 整个回测区间内的累计收益率。<br/>
                            <b>年化收益</b> = 将总收益折算为每年的平均收益。<br/><br/>
                            <b>回测参数:</b><br/>
                            T+1执行延迟 (信号日下单，次日开盘成交)。<br/>
                            买入成本7.6bps, 卖出成本13.2bps (含佣金+印花税)。<br/>
                            等权配置，每只股票1/N权重。<br/><br/>
                            <b>注意:</b> 回测收益是<b>历史模拟</b>结果，不代表未来实际收益。实盘中还需考虑流动性冲击、涨跌停限制等因素。
                          </div>
                        </template>
                        <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                  </template>
                  <div v-if="quantResult.backtest?.success">
                    <el-row :gutter="16" style="margin-bottom: 16px;">
                      <el-col :span="6">
                        <div class="quant-stat-item">
                          <span class="quant-stat-label">总收益</span>
                          <span class="quant-stat-value" :style="{ color: quantResult.backtest.total_return >= 0 ? '#f56c6c' : '#67c23a', fontSize: '20px' }">
                            {{ quantResult.backtest.total_return }}%
                          </span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="quant-stat-item">
                          <span class="quant-stat-label">年化收益</span>
                          <span class="quant-stat-value" :style="{ color: quantResult.backtest.annual_return >= 0 ? '#f56c6c' : '#67c23a', fontSize: '20px' }">
                            {{ quantResult.backtest.annual_return }}%
                          </span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="quant-stat-item">
                          <span class="quant-stat-label">Sharpe</span>
                          <span class="quant-stat-value" style="font-size: 20px;">{{ quantResult.backtest.sharpe_ratio }}</span>
                        </div>
                      </el-col>
                      <el-col :span="6">
                        <div class="quant-stat-item">
                          <span class="quant-stat-label">最大回撤</span>
                          <span class="quant-stat-value" style="color: #67c23a; font-size: 20px;">{{ quantResult.backtest.max_drawdown }}%</span>
                        </div>
                      </el-col>
                    </el-row>
                    <el-row :gutter="16" style="margin-bottom: 12px;">
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">胜率</span><span class="quant-stat-value">{{ quantResult.backtest.win_rate }}%</span></div>
                      </el-col>
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">年化波动</span><span class="quant-stat-value">{{ quantResult.backtest.annual_volatility }}%</span></div>
                      </el-col>
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">Calmar</span><span class="quant-stat-value">{{ quantResult.backtest.calmar_ratio }}</span></div>
                      </el-col>
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">年换手率</span><span class="quant-stat-value">{{ quantResult.backtest.annual_turnover }}%</span></div>
                      </el-col>
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">总费用</span><span class="quant-stat-value">{{ quantResult.backtest.total_cost_pct }}%</span></div>
                      </el-col>
                      <el-col :span="4">
                        <div class="quant-stat-item"><span class="quant-stat-label">调仓次数</span><span class="quant-stat-value">{{ quantResult.backtest.n_rebalances }}</span></div>
                      </el-col>
                    </el-row>
                    <!-- Equity curve chart -->
                    <div ref="quantEquityRef" style="width: 100%; height: 280px;"></div>
                  </div>
                  <div v-else style="text-align: center; padding: 40px; color: #999;">
                    {{ quantResult.backtest?.error || '回测数据不足' }}
                  </div>
                </el-card>
              </el-col>
              <el-col :span="8">
                <el-card class="section-card" shadow="hover">
                  <template #header>
                    <div class="card-header-row">
                      <span class="card-title">因子权重雷达</span>
                      <el-tooltip placement="top-end">
                        <template #content>
                          <div style="max-width: 360px; line-height: 1.6;">
                            雷达图展示按类别汇总的因子权重分布。每个方向代表一个因子类别 (价值、动量、反转、量能、波动、技术、资金流、统计)，面积越大表示该类因子在组合信号中的贡献越大。<br/><br/>
                            权重由IC衰减加权法计算: IC_IR越大、近期IC越稳定的因子权重越高。均衡分布的雷达通常更稳健。
                          </div>
                        </template>
                        <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                      </el-tooltip>
                    </div>
                  </template>
                  <div ref="quantRadarRef" style="width: 100%; height: 320px;"></div>
                </el-card>
              </el-col>
            </el-row>

            <!-- Period Returns (per rebalance cycle) -->
            <el-card class="section-card" shadow="hover" v-if="quantResult.backtest?.period_returns?.length">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">换仓周期收益 (每{{ quantResult.portfolio?.config?.rebalance_freq || 10 }}个交易日)</span>
                  <el-tooltip placement="top-end">
                    <template #content>
                      <div style="max-width: 380px; line-height: 1.6;">
                        <b>周期收益解读:</b><br/>
                        每格对应一个换仓周期 ({{ quantResult.portfolio?.config?.rebalance_freq || 10 }}个交易日)，显示该周期内持仓组合的等权平均收益率 (扣除交易成本)。<br/><br/>
                        <b>颜色含义 (A股惯例: 红涨绿跌):</b><br/>
                        红底红字 (>=0%): 正收益周期 (深红底 >=+3% 表示强势期)<br/>
                        绿底绿字 (&lt;0%): 回撤周期 (深绿底 <=-3% 表示显著回撤)<br/><br/>
                        <b>关注要点:</b><br/>
                        1. 正收益周期占比即为"胜率"<br/>
                        2. 连续亏损期数反映策略在熊市/震荡市的抗压能力<br/>
                        3. 单期极端亏损 (>-5%) 提示策略可能缺乏风控
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <div style="display: flex; flex-wrap: wrap; gap: 4px;">
                <div v-for="(p, idx) in quantResult.backtest.period_returns" :key="p.period ?? p.month ?? idx"
                  :style="{
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontSize: '11px',
                    fontWeight: 600,
                    background: p.return >= 3 ? '#fef0f0' : p.return >= 0 ? '#f5f7fa' : p.return >= -3 ? '#f0f9eb' : '#e1f3d8',
                    color: p.return >= 0 ? '#f56c6c' : '#67c23a',
                    cursor: 'default',
                  }"
                  :title="p.period != null ? `第${p.period}期: ${p.start} ~ ${p.end}, 收益: ${p.return}%` : `${p.month}: ${p.return}%`"
                >
                  <template v-if="p.period != null">{{ p.period }}: {{ p.return > 0 ? '+' : '' }}{{ p.return }}%</template>
                  <template v-else>{{ (p.month || '').substring(5) }}: {{ p.return > 0 ? '+' : '' }}{{ p.return }}%</template>
                </div>
              </div>
              <div style="margin-top: 8px; font-size: 11px; color: #bbb;">
                正收益期: {{ quantResult.backtest.period_returns.filter(p => p.return > 0).length }} /
                {{ quantResult.backtest.period_returns.length }}
                ({{ (quantResult.backtest.period_returns.filter(p => p.return > 0).length / quantResult.backtest.period_returns.length * 100).toFixed(0) }}%)
                | 期均收益: {{ (quantResult.backtest.period_returns.reduce((s, p) => s + p.return, 0) / quantResult.backtest.period_returns.length).toFixed(2) }}%
                | 最大单期: {{ Math.max(...quantResult.backtest.period_returns.map(p => p.return)).toFixed(1) }}%
                | 最小单期: {{ Math.min(...quantResult.backtest.period_returns.map(p => p.return)).toFixed(1) }}%
              </div>
            </el-card>

            <!-- ML Model Details -->
            <el-card class="section-card" shadow="hover" v-if="quantResult.ml_model?.available">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">ML模型详情 ({{ quantResult.ml_model.model_type }})</span>
                  <el-tooltip placement="top-end">
                    <template #content>
                      <div style="max-width: 420px; line-height: 1.6;">
                        使用机器学习模型对因子进行非线性增强，捕获因子间的交互效应。<br/><br/>
                        <b>模型</b>: 优先使用LightGBM梯度提升树，若不可用则回退到sklearn GBM。输入为所有有效因子的横截面排名值，目标为未来10日收益方向（涨/跌二分类）。<br/>
                        <b>训练/测试</b>: 前80%交易日为训练集，后20%为测试集（时间序列切分，防止未来信息泄露）。<br/><br/>
                        <b>Accuracy</b>: 模型在测试集上预测涨跌方向的正确率。>50%即有正向贡献。<br/>
                        <b>Precision</b>: 模型预测"涨"时的准确率 — 减少错误买入信号。<br/>
                        <b>Recall</b>: 实际上涨股票中被模型捕获的比例 — 衡量遗漏率。<br/>
                        <b>F1</b>: Precision与Recall的调和平均，综合评估模型质量。<br/><br/>
                        <b>特征重要性</b>: 模型内部各因子对预测的贡献度排名，值越大说明该因子在非线性组合中越重要。<br/>
                        <b>混合权重</b>: ML预测信号占最终综合评分的比例（通常40%），其余为IC加权线性信号。
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <el-row :gutter="16">
                <el-col :span="3" v-for="metric in ['accuracy', 'precision', 'recall', 'f1']" :key="metric">
                  <div class="quant-stat-item">
                    <span class="quant-stat-label">{{ metric.charAt(0).toUpperCase() + metric.slice(1) }}</span>
                    <span class="quant-stat-value">{{ (quantResult.ml_model[metric] * 100).toFixed(1) }}%</span>
                  </div>
                </el-col>
                <el-col :span="3">
                  <div class="quant-stat-item"><span class="quant-stat-label">训练集</span><span class="quant-stat-value">{{ quantResult.ml_model.train_size }}</span></div>
                </el-col>
                <el-col :span="3">
                  <div class="quant-stat-item"><span class="quant-stat-label">测试集</span><span class="quant-stat-value">{{ quantResult.ml_model.test_size }}</span></div>
                </el-col>
              </el-row>
              <!-- Feature importance -->
              <div v-if="quantResult.ml_model.feature_importance?.length" style="margin-top: 16px;">
                <div style="font-size: 13px; font-weight: 600; margin-bottom: 8px;">特征重要性排名 Top {{ quantResult.ml_model.feature_importance.length }}</div>
                <div v-for="fi in quantResult.ml_model.feature_importance" :key="fi.factor" class="quant-fi-bar">
                  <span class="quant-fi-label">{{ fi.factor }}</span>
                  <el-progress :percentage="Math.round(fi.importance * 100 / (quantResult.ml_model.feature_importance[0]?.importance || 1))" :stroke-width="12" :show-text="false"
                    :color="CATEGORY_COLORS[quantResult.factors?.find(f => f.name === fi.factor)?.category] || '#409eff'"
                    style="flex: 1; margin: 0 8px;" />
                  <span class="quant-fi-value">{{ (fi.importance * 100).toFixed(1) }}%</span>
                </div>
              </div>
            </el-card>

            <!-- Valid Factors Table -->
            <el-card class="section-card" shadow="hover">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">有效因子 ({{ quantValidFactors.length }})</span>
                  <el-tooltip placement="top-end">
                    <template #content>
                      <div style="max-width: 400px; line-height: 1.6;">
                        通过IC检验的有效因子列表，按权重排序。这些因子被纳入组合信号计算。<br/><br/>
                        <b>IC均值</b>: 因子值与未来10日收益的横截面Spearman秩相关系数均值。|IC|>=0.03为有效。<br/>
                        <b>IC_IR</b>: IC均值/IC标准差，衡量预测力稳定性。>=0.3为有效，>=0.5为优秀。<br/>
                        <b>一致性</b>: IC值在同一方向的交易日占比，>=55%为有效。<br/>
                        <b>权重</b>: 基于IC衰减加权计算，IC_IR越大、近期越稳定的因子权重越高。<br/>
                        <b>方向</b>: 正向=因子值越大收益越高；反向=因子值越小收益越高。
                      </div>
                    </template>
                    <el-icon style="color: #909399; cursor: help; font-size: 16px;"><InfoFilled /></el-icon>
                  </el-tooltip>
                </div>
              </template>
              <el-table :data="quantValidFactors" size="small" stripe style="width: 100%;">
                <el-table-column prop="name" label="因子" width="150">
                  <template #header>
                    <el-tooltip content="因子名称。每个因子是一个量化指标，从不同维度衡量股票的特征。系统共50个因子，涵盖8类: 价值、动量、反转、量能、波动、技术、资金流、统计。" placement="top">
                      <span style="cursor: help;">因子</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <el-tooltip :content="row.description" placement="right" :show-after="300">
                      <span style="cursor: help;">
                        <span style="font-weight: 600;">{{ row.label || row.name }}</span>
                        <span v-if="row.label" style="color: #999; font-size: 10px; margin-left: 4px;">{{ row.name }}</span>
                      </span>
                    </el-tooltip>
                  </template>
                </el-table-column>
                <el-table-column prop="category_label" label="类别" width="80">
                  <template #default="{ row }">
                    <el-tag size="small" :color="CATEGORY_COLORS[row.category]" style="color: #fff; border: none;">{{ row.category_label }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="ic_direction_label" width="65" align="center">
                  <template #header>
                    <el-tooltip content="因子方向。正向: 因子值越大，预期未来收益越高；反向: 因子值越大，预期未来收益越低。系统会自动识别方向并调整权重符号。" placement="top">
                      <span style="cursor: help;">方向</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <el-tag :type="row.ic_direction >= 0 ? 'success' : 'warning'" size="small">{{ row.ic_direction_label }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="weight" width="90" align="right" sortable>
                  <template #header>
                    <el-tooltip content="因子在组合信号中的权重占比。基于IC衰减加权(指数衰减)计算: |IC_IR|越大、近期IC越稳定的因子权重越高。正值=因子值越大→预期收益越高; 负值=因子值越大→预期收益越低。" placement="top">
                      <span style="cursor: help;">权重</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <span :style="{ fontWeight: Math.abs(row.weight) > 0.05 ? 700 : 400 }">{{ (row.weight * 100).toFixed(1) }}%</span>
                  </template>
                </el-table-column>
                <el-table-column prop="ic_mean" width="90" align="right">
                  <template #header>
                    <el-tooltip content="IC均值 (Information Coefficient): 每个交易日计算因子值与未来10日收益的横截面(所有股票)Spearman秩相关系数，然后取均值。|IC|>0.03为有效因子。正IC(红色)=因子值越大收益越高; 负IC(绿色)=反向关系。" placement="top">
                      <span style="cursor: help;">IC均值</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <span :style="{ color: row.ic_mean > 0 ? '#f56c6c' : '#67c23a' }">{{ row.ic_mean?.toFixed(4) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="ic_ir" width="80" align="right">
                  <template #header>
                    <el-tooltip content="IC_IR (IC信息比率) = IC均值 / IC标准差。衡量因子预测力的稳定性。|IC_IR|>=0.3为有效因子门槛(入选组合)，>=0.5为优秀因子(权重更高)。IC_IR越高，因子在不同时期的表现越一致。" placement="top">
                      <span style="cursor: help;">IC_IR</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">
                    <span :style="{ color: Math.abs(row.ic_ir) >= 0.5 ? '#409eff' : '#333', fontWeight: Math.abs(row.ic_ir) >= 0.5 ? 700 : 400 }">{{ row.ic_ir?.toFixed(3) }}</span>
                  </template>
                </el-table-column>
                <el-table-column prop="ic_consistency" width="80" align="right">
                  <template #header>
                    <el-tooltip content="方向一致性: IC值在多少比例的交易日上保持同一方向（正或负）。>=55%为有效门槛，表示因子在超过一半的交易日上预测方向稳定，不易翻转。" placement="top">
                      <span style="cursor: help;">一致性</span>
                    </el-tooltip>
                  </template>
                  <template #default="{ row }">{{ (row.ic_consistency * 100).toFixed(1) }}%</template>
                </el-table-column>
                <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
              </el-table>
            </el-card>

            <!-- Invalid Factors (collapsed) -->
            <el-card class="section-card" shadow="hover" v-if="quantInvalidFactors.length > 0" style="opacity: 0.7;">
              <template #header>
                <span class="card-title">无效因子 ({{ quantInvalidFactors.length }}) <span style="font-size: 12px; color: #999; font-weight: normal;">— |IC均值|&lt;0.03 或 |IC_IR|&lt;0.3 或 一致性&lt;55%</span></span>
              </template>
              <el-table :data="quantInvalidFactors" size="small" stripe style="width: 100%;">
                <el-table-column prop="name" label="因子" width="150" />
                <el-table-column prop="category_label" label="类别" width="80">
                  <template #default="{ row }">
                    <el-tag size="small" :color="CATEGORY_COLORS[row.category]" style="color: #fff; border: none;">{{ row.category_label }}</el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="ic_ir" label="IC_IR" width="80" align="right">
                  <template #default="{ row }">{{ row.ic_ir?.toFixed(3) }}</template>
                </el-table-column>
                <el-table-column prop="ic_consistency" label="一致性" width="80" align="right">
                  <template #default="{ row }">{{ (row.ic_consistency * 100).toFixed(1) }}%</template>
                </el-table-column>
                <el-table-column label="失效原因" min-width="200">
                  <template #default="{ row }">
                    <span style="color: #f56c6c; font-size: 12px;">
                      {{ Math.abs(row.ic_ir) < 0.3 ? `|IC_IR|=${Math.abs(row.ic_ir).toFixed(3)}<0.3` : '' }}
                      {{ row.ic_consistency < 0.55 ? `一致性=${(row.ic_consistency * 100).toFixed(1)}%<55%` : '' }}
                    </span>
                  </template>
                </el-table-column>
                <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
              </el-table>
            </el-card>

          </div>

          <!-- Empty state -->
          <div v-if="!quantResult && !quantLoading && !quantError && !quantViewingRunId" style="text-align: center; padding: 80px 0; color: #999;">
            <el-icon :size="48"><Histogram /></el-icon>
            <p style="margin-top: 12px; font-size: 15px;">配置参数后点击"开始分析"</p>
            <p style="font-size: 12px;">系统将对全A股进行横截面因子分析，计算50个量化因子的横截面IC，构建Top-N投资组合并进行历史回测</p>
          </div>

          </template><!-- end quantTab === 'new' -->

          <!-- ===== Tab: History ===== -->
          <template v-if="quantTab === 'history'">
            <el-card class="section-card" shadow="hover">
              <template #header>
                <div class="card-header-row">
                  <span class="card-title">分析历史记录 ({{ quantHistoryTotal }})</span>
                  <el-button type="primary" link @click="loadQuantHistory">刷新</el-button>
                </div>
              </template>
              <el-table :data="quantHistory" v-loading="quantHistoryLoading" stripe size="small" style="width: 100%;">
                <el-table-column prop="name" label="名称" min-width="160" show-overflow-tooltip>
                  <template #default="{ row }">
                    <a class="stock-link" @click="viewQuantResult(row.run_id)">{{ row.name || row.run_id?.substring(0, 8) }}</a>
                  </template>
                </el-table-column>
                <el-table-column label="策略" width="130" show-overflow-tooltip>
                  <template #default="{ row }">
                    <span v-if="row.filters_applied?.preset">
                      {{ (quantPresets.find(p => p.name === row.filters_applied.preset) || {}).label || row.filters_applied.preset }}
                    </span>
                    <span v-else-if="row.filters_applied?.industries">
                      {{ Array.isArray(row.filters_applied.industries) ? row.filters_applied.industries.join(',') : row.filters_applied.industries }}
                    </span>
                    <span v-else style="color: #999;">自定义</span>
                  </template>
                </el-table-column>
                <el-table-column label="Top-N" width="65" align="center">
                  <template #default="{ row }">{{ row.config?.top_n || '-' }}</template>
                </el-table-column>
                <el-table-column label="周期" width="55" align="center">
                  <template #default="{ row }">
                    <span v-if="row.config?.rebalance_freq">{{ row.config.rebalance_freq }}天</span>
                    <span v-else style="color: #999;">-</span>
                  </template>
                </el-table-column>
                <el-table-column prop="analysis_date" label="分析日期" width="110" />
                <el-table-column label="宇宙" width="70" align="center">
                  <template #default="{ row }">{{ row.universe_info?.in_universe || '-' }}</template>
                </el-table-column>
                <el-table-column label="组合" width="55" align="center">
                  <template #default="{ row }">{{ row.portfolio_size || '-' }}</template>
                </el-table-column>
                <el-table-column prop="total_return" label="总收益" width="90" align="right">
                  <template #default="{ row }">
                    <span v-if="row.total_return != null" :style="{ color: row.total_return >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                      {{ row.total_return > 0 ? '+' : '' }}{{ row.total_return }}%
                    </span>
                    <span v-else style="color: #999;">-</span>
                  </template>
                </el-table-column>
                <el-table-column prop="annual_return" label="年化" width="80" align="right">
                  <template #default="{ row }">
                    <span v-if="row.annual_return != null" :style="{ color: row.annual_return >= 0 ? '#f56c6c' : '#67c23a' }">
                      {{ row.annual_return }}%
                    </span>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column prop="sharpe_ratio" label="Sharpe" width="75" align="right">
                  <template #default="{ row }">{{ row.sharpe_ratio ?? '-' }}</template>
                </el-table-column>
                <el-table-column prop="max_drawdown" label="最大回撤" width="90" align="right">
                  <template #default="{ row }">
                    <span v-if="row.max_drawdown != null" style="color: #67c23a;">{{ row.max_drawdown }}%</span>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <el-table-column label="自动迭代" width="110" align="center">
                  <template #default="{ row }">
                    <el-switch
                      :model-value="row.auto_iterate || false"
                      size="small"
                      @change="toggleQuantIterate(row)"
                    />
                    <span v-if="row.total_iterations > 0" style="margin-left: 4px; color: #409eff; font-size: 11px;">
                      {{ row.total_iterations }}期
                    </span>
                  </template>
                </el-table-column>
                <el-table-column label="实盘NAV" width="100" align="right">
                  <template #default="{ row }">
                    <template v-if="row.auto_iterate && row.live_nav != null">
                      <span :style="{ color: (row.live_return_pct || 0) >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                        {{ row.live_nav?.toFixed(4) }}
                      </span>
                    </template>
                    <span v-else style="color: #999;">-</span>
                  </template>
                </el-table-column>
                <el-table-column label="操作" width="200" align="center" fixed="right">
                  <template #default="{ row }">
                    <el-button type="primary" link size="small" @click="viewQuantResult(row.run_id)">查看</el-button>
                    <el-button v-if="row.auto_iterate" type="success" link size="small" @click="openQuantIterateDialog(row)">迭代</el-button>
                    <el-button type="warning" link size="small" @click="openQuantEditDialog(row)">编辑</el-button>
                    <el-button type="danger" link size="small" @click="deleteQuantResult(row.run_id)">删除</el-button>
                  </template>
                </el-table-column>
              </el-table>
              <div v-if="quantHistoryTotal > 20" style="margin-top: 12px; text-align: right;">
                <el-pagination
                  v-model:current-page="quantHistoryPage"
                  :page-size="20"
                  :total="quantHistoryTotal"
                  layout="prev, pager, next"
                  @current-change="loadQuantHistory"
                />
              </div>
            </el-card>

            <!-- Edit dialog -->
            <el-dialog v-model="quantEditDialogVisible" title="编辑分析记录" width="480px">
              <el-form label-width="80px">
                <el-form-item label="名称">
                  <el-input v-model="quantEditForm.name" placeholder="分析名称" />
                </el-form-item>
                <el-form-item label="备注">
                  <el-input v-model="quantEditForm.notes" type="textarea" :rows="3" placeholder="备注信息" />
                </el-form-item>
              </el-form>
              <template #footer>
                <el-button @click="quantEditDialogVisible = false">取消</el-button>
                <el-button type="primary" @click="saveQuantEdit">保存</el-button>
              </template>
            </el-dialog>

            <!-- Iteration timeline dialog -->
            <el-dialog v-model="quantIterateDialogVisible" title="自动迭代管理" width="1000px" top="3vh">
              <div v-if="quantIterateInfo" style="margin-bottom: 16px;">
                <el-descriptions :column="4" size="small" border>
                  <el-descriptions-item label="策略名称">{{ quantIterateInfo.name || quantIterateInfo.run_id?.substring(0, 8) }}</el-descriptions-item>
                  <el-descriptions-item label="迭代期数">{{ quantIterateInfo.total_iterations || 0 }} 期</el-descriptions-item>
                  <el-descriptions-item label="实盘NAV">
                    <span v-if="quantIterateInfo.live_nav != null" :style="{ color: (quantIterateInfo.live_return_pct || 0) >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                      {{ quantIterateInfo.live_nav?.toFixed(4) }}
                      ({{ (quantIterateInfo.live_return_pct || 0) >= 0 ? '+' : '' }}{{ (quantIterateInfo.live_return_pct || 0).toFixed(2) }}%)
                    </span>
                    <span v-else>-</span>
                  </el-descriptions-item>
                  <el-descriptions-item label="下次迭代">{{ quantIterateInfo.next_iterate_date || '-' }}</el-descriptions-item>
                </el-descriptions>
                <div style="margin-top: 12px; display: flex; align-items: center; gap: 12px;">
                  <el-button type="primary" :loading="quantIterating" @click="triggerQuantIteration">
                    {{ quantIterating ? '迭代中...' : '立即执行迭代' }}
                  </el-button>
                  <el-button @click="loadQuantIterations(quantIterateRunId)">刷新</el-button>
                  <el-tag v-if="quantIterateInfo.iterate_status" :type="quantIterateInfo.iterate_status === 'running' ? 'warning' : quantIterateInfo.iterate_status === 'error' ? 'danger' : 'success'" size="small">
                    {{ quantIterateInfo.iterate_status === 'running' ? '运行中' : quantIterateInfo.iterate_status === 'error' ? '出错' : '就绪' }}
                  </el-tag>
                </div>
              </div>

              <!-- Iteration detail: portfolio selector + detail view -->
              <div v-if="quantIterations.length > 0" style="margin-bottom: 12px;">
                <div style="display: flex; align-items: center; gap: 12px; margin-bottom: 8px;">
                  <span style="font-size: 13px; font-weight: 600; white-space: nowrap;">查看期数:</span>
                  <el-select v-model="quantIterViewNum" size="small" style="width: 320px;" @change="onQuantIterViewChange">
                    <el-option
                      v-for="it in quantIterations"
                      :key="it.iteration_num"
                      :value="it.iteration_num"
                      :label="`第${it.iteration_num}期 (${it.start_date}${it.end_date ? ' ~ ' + it.end_date : ' 进行中'}) - ${it.portfolio_size}只`"
                    />
                  </el-select>
                  <el-tag v-if="quantIterViewItem" :type="quantIterViewItem.status === 'active' ? 'warning' : quantIterViewItem.status === 'completed' ? 'success' : 'danger'" size="small">
                    {{ quantIterViewItem.status === 'active' ? '当前持仓' : '已换仓' }}
                  </el-tag>
                  <span v-if="quantIterViewItem && quantIterViewItem.period_return_pct != null" style="font-size: 12px;">
                    期间收益:
                    <b :style="{ color: quantIterViewItem.period_return_pct >= 0 ? '#f56c6c' : '#67c23a' }">
                      {{ quantIterViewItem.period_return_pct > 0 ? '+' : '' }}{{ quantIterViewItem.period_return_pct }}%
                    </b>
                  </span>
                  <span v-if="quantIterViewItem && quantIterViewItem.nav != null" style="font-size: 12px;">
                    NAV: <b>{{ quantIterViewItem.nav.toFixed(4) }}</b>
                  </span>
                </div>

                <!-- Buy/Sell changes for this iteration -->
                <div v-if="quantIterViewItem && (quantIterViewItem.new_buys?.length || quantIterViewItem.new_sells?.length)" style="margin-bottom: 8px; font-size: 12px; line-height: 1.8;">
                  <span v-if="quantIterViewItem.new_buys?.length" style="color: #f56c6c;">
                    买入({{ quantIterViewItem.new_buys.length }}):
                    {{ quantIterViewBuyNames }}
                  </span>
                  <span v-if="quantIterViewItem.new_buys?.length && quantIterViewItem.new_sells?.length" style="margin: 0 8px; color: #ddd;">|</span>
                  <span v-if="quantIterViewItem.new_sells?.length" style="color: #67c23a;">
                    卖出({{ quantIterViewItem.new_sells.length }}):
                    {{ quantIterViewSellNames }}
                  </span>
                </div>
              </div>

              <!-- Selected iteration's portfolio table -->
              <div v-if="quantIterViewItem && quantIterViewItem.portfolio && quantIterViewItem.portfolio.length > 0">
                <el-table :data="quantIterViewItem.portfolio" size="small" stripe max-height="350" style="width: 100%;">
                  <el-table-column type="index" label="#" width="45" align="center" />
                  <el-table-column prop="stock_code" label="代码" width="80">
                    <template #default="{ row }">
                      <span style="font-family: monospace; font-weight: 600;">{{ row.stock_code }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="stock_name" label="名称" width="100" />
                  <el-table-column prop="industry" label="行业" width="100">
                    <template #default="{ row }">
                      <el-tag size="small" type="info">{{ row.industry || '-' }}</el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column prop="score" label="综合分" width="80" align="right" sortable>
                    <template #default="{ row }">
                      <span :style="{ color: (row.score || 0) >= 70 ? '#f56c6c' : (row.score || 0) >= 50 ? '#e6a23c' : '#67c23a', fontWeight: 600 }">
                        {{ row.score != null ? row.score.toFixed?.(1) ?? row.score : '-' }}
                      </span>
                    </template>
                  </el-table-column>
                  <el-table-column label="变动" width="70" align="center">
                    <template #default="{ row }">
                      <el-tag v-if="quantIterViewItem.new_buys?.includes(row.stock_code)" type="danger" size="small">新买入</el-tag>
                      <span v-else style="color: #999; font-size: 11px;">持有</span>
                    </template>
                  </el-table-column>
                  <!-- Show return if this iteration is completed and has stock_returns -->
                  <el-table-column v-if="quantIterViewItem.stock_returns?.length" label="期间收益" width="90" align="right">
                    <template #default="{ row }">
                      <template v-if="quantIterViewItem.stock_returns">
                        <span v-for="sr in quantIterViewItem.stock_returns.filter(s => s.code === row.stock_code)" :key="sr.code"
                          :style="{ color: (sr.return_pct || 0) >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                          {{ sr.return_pct != null ? ((sr.return_pct > 0 ? '+' : '') + sr.return_pct + '%') : '-' }}
                        </span>
                      </template>
                    </template>
                  </el-table-column>
                </el-table>
              </div>
              <el-empty v-else-if="quantIterViewItem" description="该期无组合数据" :image-size="60" />

              <!-- Iteration timeline summary (collapsible) -->
              <el-divider content-position="left" style="margin-top: 20px;">
                <span style="font-size: 12px; color: #909399;">全部迭代时间线 ({{ quantIterations.length }}期)</span>
              </el-divider>
              <el-table :data="quantIterations" v-loading="quantIterationsLoading" stripe size="small" style="width: 100%;" max-height="300">
                <el-table-column prop="iteration_num" label="#" width="50" align="center" />
                <el-table-column prop="start_date" label="开始" width="105" />
                <el-table-column prop="end_date" label="结束" width="105">
                  <template #default="{ row }">{{ row.end_date || '(进行中)' }}</template>
                </el-table-column>
                <el-table-column prop="portfolio_size" label="持仓" width="55" align="center" />
                <el-table-column label="调仓" width="80" align="center">
                  <template #default="{ row }">
                    <span style="color: #f56c6c;">+{{ (row.new_buys || []).length }}</span>
                    <span style="margin: 0 2px;">/</span>
                    <span style="color: #67c23a;">-{{ (row.new_sells || []).length }}</span>
                  </template>
                </el-table-column>
                <el-table-column label="期间收益" width="90" align="right">
                  <template #default="{ row }">
                    <span v-if="row.period_return_pct != null" :style="{ color: row.period_return_pct >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                      {{ row.period_return_pct > 0 ? '+' : '' }}{{ row.period_return_pct }}%
                    </span>
                    <span v-else style="color: #999;">-</span>
                  </template>
                </el-table-column>
                <el-table-column label="累计收益" width="90" align="right">
                  <template #default="{ row }">
                    <span v-if="row.cumulative_return_pct != null" :style="{ color: row.cumulative_return_pct >= 0 ? '#f56c6c' : '#67c23a', fontWeight: 600 }">
                      {{ row.cumulative_return_pct > 0 ? '+' : '' }}{{ row.cumulative_return_pct }}%
                    </span>
                    <span v-else style="color: #999;">-</span>
                  </template>
                </el-table-column>
                <el-table-column prop="nav" label="NAV" width="75" align="right">
                  <template #default="{ row }">{{ row.nav != null ? row.nav.toFixed(4) : '-' }}</template>
                </el-table-column>
                <el-table-column prop="status" label="状态" width="75" align="center">
                  <template #default="{ row }">
                    <el-tag :type="row.status === 'active' ? 'warning' : row.status === 'completed' ? 'success' : 'danger'" size="small">
                      {{ row.status === 'active' ? '进行中' : row.status === 'completed' ? '已完成' : row.status }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column label="" width="60" align="center">
                  <template #default="{ row }">
                    <el-button type="primary" link size="small" @click="quantIterViewNum = row.iteration_num; onQuantIterViewChange()">查看</el-button>
                  </template>
                </el-table-column>
              </el-table>
            </el-dialog>
          </template><!-- end quantTab === 'history' -->

        </div>

        <!-- ==================== Section 9: User Management ==================== -->
        <div v-if="activeMenu === 'users'">
          <div class="section-header">
            <h2>用户管理</h2>
            <el-button type="primary" :icon="Plus" @click="openCreateUser">新建用户</el-button>
          </div>

          <el-card class="section-card" shadow="hover">
            <el-table :data="userList" v-loading="userListLoading" stripe style="width: 100%;">
              <el-table-column prop="username" label="用户名" width="150" />
              <el-table-column prop="display_name" label="显示名称" width="150">
                <template #default="{ row }">{{ row.display_name || '-' }}</template>
              </el-table-column>
              <el-table-column prop="role" label="角色" width="100">
                <template #default="{ row }">
                  <el-tag :type="row.role === 'admin' ? 'danger' : 'info'" size="small">{{ row.role === 'admin' ? '管理员' : '普通用户' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="is_active" label="状态" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.is_active ? 'success' : 'danger'" size="small">{{ row.is_active ? '启用' : '禁用' }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="权限" min-width="300">
                <template #default="{ row }">
                  <template v-if="row.role === 'admin'">
                    <el-tag size="small" type="warning">全部权限</el-tag>
                  </template>
                  <template v-else>
                    <el-tag v-for="(val, key) in row.permissions" :key="key" v-show="val" size="small" style="margin: 2px 4px 2px 0;">{{ permissionLabels[key] || key }}</el-tag>
                  </template>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="200" fixed="right">
                <template #default="{ row }">
                  <el-button size="small" @click="openEditUser(row)">编辑</el-button>
                  <el-button size="small" :type="row.is_active ? 'warning' : 'success'" @click="toggleUserActive(row)">{{ row.is_active ? '禁用' : '启用' }}</el-button>
                  <el-button size="small" type="danger" @click="deleteUser(row)" :disabled="row.role === 'admin' && userList.filter(u => u.role === 'admin').length <= 1">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </div>

      </el-main>
    </el-container>

    <!-- Change Password Dialog -->
    <el-dialog v-model="showChangePassword" title="修改密码" width="400px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="原密码">
          <el-input v-model="changePasswordForm.old_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="新密码">
          <el-input v-model="changePasswordForm.new_password" type="password" show-password />
        </el-form-item>
        <el-form-item label="确认密码">
          <el-input v-model="changePasswordForm.confirm_password" type="password" show-password @keyup.enter="doChangePassword" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showChangePassword = false">取消</el-button>
        <el-button type="primary" :loading="changePasswordLoading" @click="doChangePassword">确认修改</el-button>
      </template>
    </el-dialog>

    <!-- User Create/Edit Dialog -->
    <el-dialog v-model="showUserDialog" :title="userDialogMode === 'create' ? '新建用户' : '编辑用户'" width="500px" :close-on-click-modal="false">
      <el-form label-width="80px">
        <el-form-item label="用户名">
          <el-input v-model="userForm.username" :disabled="userDialogMode === 'edit'" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item :label="userDialogMode === 'create' ? '密码' : '新密码'">
          <el-input v-model="userForm.password" type="password" show-password :placeholder="userDialogMode === 'edit' ? '留空则不修改' : '设置密码'" />
        </el-form-item>
        <el-form-item label="显示名称">
          <el-input v-model="userForm.display_name" placeholder="可选，显示在界面上的名称" />
        </el-form-item>
        <el-form-item label="角色">
          <el-radio-group v-model="userForm.role">
            <el-radio value="user">普通用户</el-radio>
            <el-radio value="admin">管理员</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="功能权限" v-if="userForm.role !== 'admin'">
          <div style="display: flex; flex-wrap: wrap; gap: 8px;">
            <el-checkbox v-for="key in allPermissionKeys" :key="key" v-model="userForm.permissions[key]">{{ permissionLabels[key] || key }}</el-checkbox>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUserDialog = false">取消</el-button>
        <el-button type="primary" :loading="userFormLoading" @click="saveUser">{{ userDialogMode === 'create' ? '创建' : '保存' }}</el-button>
      </template>
    </el-dialog>

  </el-container>
</template>
<style scoped>
.app-container {
  height: 100vh;
  overflow: hidden;
}

.app-header {
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 60px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  z-index: 10;
}

.header-content {
  display: flex;
  align-items: center;
}

.header-title {
  color: #ffffff;
  font-size: 20px;
  font-weight: 600;
  margin: 0;
  letter-spacing: 1px;
}

.main-container {
  height: calc(100vh - 60px);
  overflow: hidden;
}

.app-sidebar {
  background-color: #304156;
  overflow-y: auto;
  border-right: none;
}

.sidebar-menu {
  border-right: none;
  height: 100%;
}

.sidebar-menu .el-menu-item {
  font-size: 14px;
  height: 50px;
  line-height: 50px;
}

.sidebar-menu .el-menu-item.is-active {
  background-color: #263445 !important;
  border-left: 3px solid #409eff;
}

.app-main {
  background-color: #f0f2f5;
  overflow-y: auto;
  padding: 20px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.section-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}

.section-card {
  margin-bottom: 16px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  display: inline-flex;
  align-items: center;
}

.card-header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

/* Chinese stock convention: red = up, green = down */
.price-up {
  color: #ef5350;
  font-weight: 600;
}

.price-down {
  color: #26a65b;
  font-weight: 600;
}

/* Realtime quote panel */
.realtime-panel {
  padding: 0 0 8px 0;
}
.realtime-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid #ebeef5;
}
.realtime-price-block {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.realtime-price {
  font-size: 32px;
  font-weight: 700;
  line-height: 1;
}
.realtime-change {
  font-size: 16px;
  font-weight: 600;
}
.realtime-meta {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.realtime-source {
  font-size: 12px;
  color: #909399;
  background: #f0f2f5;
  padding: 1px 8px;
  border-radius: 10px;
}
.realtime-time {
  font-size: 12px;
  color: #909399;
}
.realtime-grid {
  margin-top: 4px;
}
.realtime-item {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed #f0f0f0;
}
.realtime-label {
  color: #909399;
  font-size: 13px;
}
.realtime-val {
  font-weight: 600;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.quotes-intraday-section {
  margin-top: 16px;
  min-height: 100px;
}
.realtime-quote-area {
  min-height: 60px;
}

/* Favorite star */
.fav-star {
  cursor: pointer;
  font-size: 18px;
  color: #c0c4cc;
  transition: color 0.2s, transform 0.2s;
}
.fav-star:hover {
  transform: scale(1.2);
  color: #f7ba2a;
}
.fav-star.active {
  color: #f7ba2a;
}

/* K-line chart */
.kline-chart-wrapper {
  width: 100%;
}

.kline-chart {
  width: 100%;
  height: 560px;
  border-radius: 8px;
  overflow: hidden;
}

.kline-summary {
  text-align: center;
  color: #909399;
  font-size: 12px;
  margin-top: 8px;
  padding: 4px 0;
}

/* Profile info bar */
.profile-bar {
  background: linear-gradient(135deg, #f5f7fa 0%, #e8ecf1 100%);
}

.profile-item {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.profile-label {
  font-size: 11px;
  color: #909399;
  margin-bottom: 4px;
}

.profile-value {
  font-size: 14px;
  font-weight: 600;
  color: #303133;
}

/* Screener preset buttons */
.preset-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}

.preset-btn {
  height: auto !important;
  padding: 12px 16px !important;
  min-width: 150px;
}

.preset-btn-content {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.4;
}

.preset-btn-content small {
  font-size: 11px;
  opacity: 0.7;
  margin-top: 4px;
}

/* Kline toolbar */
.kline-toolbar {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 8px;
  padding: 4px 0;
}

.ma-legend {
  display: flex;
  gap: 12px;
  font-size: 12px;
  font-weight: 600;
}

/* Stock link in screener results */
.stock-link {
  color: #409eff;
  cursor: pointer;
  text-decoration: none;
  font-weight: 500;
}
.stock-link:hover {
  text-decoration: underline;
  color: #66b1ff;
}

/* Volume pattern parameter labels */
.vp-param-label {
  font-size: 11px;
  color: #909399;
  margin-bottom: 2px;
  white-space: nowrap;
}

.vp-param-hint {
  font-size: 10px;
  color: #c0c4cc;
  margin-bottom: 4px;
  line-height: 1.3;
  white-space: normal;
}

.vp-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #606266;
  margin-bottom: 6px;
  padding-left: 2px;
  border-left: 3px solid #409eff;
  padding-left: 8px;
}

/* Screener results horizontal scroll */
.screener-table-wrap {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.screener-table-wrap .el-table .el-table__header th .cell {
  white-space: nowrap;
  padding: 0 10px;
}
.screener-table-wrap .el-table .el-table__body td .cell {
  padding: 0 10px;
}

/* AI Analysis Panel */
.ai-analysis-panel {
  padding: 8px 0;
}

/* Config page helpers */
.form-help {
  font-size: 12px;
  color: #909399;
  line-height: 1.4;
  margin-top: 4px;
}
.form-inline-help {
  font-size: 12px;
  color: #909399;
  margin-left: 8px;
}

.ai-action-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ai-report-meta {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.ai-card {
  border-radius: 8px;
}

.ai-summary-text {
  font-size: 14px;
  line-height: 1.8;
  color: #606266;
  white-space: pre-wrap;
}

.ai-metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.ai-metric-label {
  font-size: 12px;
  color: #909399;
}

.ai-trend {
  font-size: 18px;
  font-weight: 700;
  color: #303133;
}

.ai-trend-icon {
  font-size: 22px;
  margin-right: 4px;
}

.ai-sentiment-card {
  text-align: center;
}

.ai-sentiment-gauge {
  display: flex;
  justify-content: center;
  padding: 8px 0;
}

.ai-gauge-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
}

.ai-gauge-number {
  font-size: 28px;
  font-weight: 700;
  color: #303133;
}

.ai-gauge-label {
  font-size: 13px;
  color: #606266;
  margin-top: 2px;
}

.ai-strategy-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 12px;
  border-radius: 8px;
  background: #f5f7fa;
  border-bottom: 3px solid #dcdfe6;
}

.ai-strategy-buy { border-bottom-color: #409eff; }
.ai-strategy-secondary { border-bottom-color: #67c23a; }
.ai-strategy-stop { border-bottom-color: #f56c6c; }
.ai-strategy-profit { border-bottom-color: #e6a23c; }

.ai-strategy-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 8px;
}

.ai-strategy-value {
  font-size: 20px;
  font-weight: 700;
  color: #303133;
}

.ai-json-viewer {
  background: #1e1e2f;
  color: #a9b7c6;
  padding: 12px 16px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', 'Courier New', monospace;
}

/* Login Page */
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
}

.login-card {
  width: 380px;
  padding: 40px 36px 30px;
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
}

.login-header {
  text-align: center;
  margin-bottom: 32px;
}

.login-header h1 {
  margin: 12px 0 0;
  font-size: 22px;
  color: #303133;
  letter-spacing: 1px;
}

.login-form {
  margin-bottom: 0;
}

.login-error {
  color: #f56c6c;
  font-size: 13px;
  margin-bottom: 12px;
  text-align: center;
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 12px;
  color: #999;
}

/* Header user area */
.header-right {
  display: flex;
  align-items: center;
}

.header-user {
  display: flex;
  align-items: center;
  color: #fff;
  cursor: pointer;
  font-size: 14px;
  padding: 6px 12px;
  border-radius: 4px;
  transition: background 0.2s;
}

.header-user:hover {
  background: rgba(255, 255, 255, 0.1);
}
</style>

<style>
/* Global resets */
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
    'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

/* Fix Element Plus table striped rows */
.el-table .el-table__row--striped td.el-table__cell {
  background-color: #fafafa;
}

/* Strategy Trading styles */
.strategy-item {
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: all 0.2s;
}
.strategy-item:hover {
  border-color: #409eff;
  background: #f0f7ff;
}
.strategy-item.active {
  border-color: #409eff;
  background: #ecf5ff;
  box-shadow: 0 0 0 1px #409eff;
}

.step-card {
  padding: 12px;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  margin-bottom: 4px;
  border-left: 4px solid #dcdfe6;
  transition: all 0.2s;
}
.step-card.watching {
  border-left-color: #409eff;
  background: #f0f7ff;
}
.step-card.filled {
  border-left-color: #67c23a;
  background: #f0f9eb;
}
.step-card.triggered, .step-card.executing {
  border-left-color: #e6a23c;
  background: #fdf6ec;
}
.step-card.failed {
  border-left-color: #f56c6c;
  background: #fef0f0;
}

.pulse-icon {
  animation: pulse-spin 1.5s linear infinite;
}
@keyframes pulse-spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

/* Quantitative Analysis */
.quant-metric-box {
  text-align: center;
  padding: 16px;
  background: #fafafa;
  border-radius: 8px;
}
.quant-metric-label {
  font-size: 13px;
  color: #999;
  margin-bottom: 4px;
}
.quant-metric-value {
  font-size: 36px;
  font-weight: 700;
  line-height: 1.2;
}
.quant-stat-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 12px;
  background: #fafafa;
  border-radius: 4px;
  margin-bottom: 4px;
}
.quant-stat-label {
  font-size: 12px;
  color: #999;
}
.quant-stat-value {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}
.quant-fi-bar {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
}
.quant-fi-label {
  width: 120px;
  font-size: 12px;
  color: #666;
  text-align: right;
  padding-right: 8px;
  flex-shrink: 0;
}
.quant-fi-value {
  width: 50px;
  font-size: 12px;
  color: #333;
  font-weight: 600;
  flex-shrink: 0;
}
.quant-corr-table {
  border-collapse: collapse;
  font-size: 11px;
  width: 100%;
}
.quant-corr-table th,
.quant-corr-table td {
  border: 1px solid #ebeef5;
  padding: 3px 4px;
  text-align: center;
  white-space: nowrap;
}
.quant-corr-header {
  background: #f5f7fa;
  font-weight: 600;
  font-size: 10px;
  max-width: 80px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
