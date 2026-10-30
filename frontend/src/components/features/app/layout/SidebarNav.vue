<script setup lang="ts">
/**
 * SidebarNav.vue
 * 工作台左侧边栏导航（PC 端双模式 + 移动端抽屉）+ 底部 Tab 导航（移动端）
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { PanelLeft } from 'lucide-vue-next'
import BaseLogo from '@/components/base/BaseLogo.vue'
import BaseDropdown from '@/components/base/BaseDropdown.vue'
import BaseTooltip from '@/components/base/BaseTooltip.vue'

const props = defineProps({
  currentView: { type: String, required: true },
  currentSettingsSubView: { type: String, default: 'profile' },
  settingsNavItems: { type: Array, default: () => [] },
  lastWorkspaceView: { type: String, default: 'workspace' },
  currentUser: { type: Object, default: null },
  isDark: { type: Boolean, default: true },
  theme: { type: String, default: 'dark' },
  // 导航配置
  navGroups: { type: Array, required: true },
  workspaceViews: { type: Object, required: true },
  collapsedGroups: { type: Object, required: true },
  projects: { type: Array, default: () => [] },
  activeProjectId: { type: [String, Number], default: null },
  loadingProjects: { type: Boolean, default: false },
  // 指示器
  navRef: { type: Object, default: null },
  navBtnRefs: { type: Object, required: true },
  indicatorStyle: { type: Object, default: () => ({}) },
  indicatorTransition: { type: Boolean, default: false },
  // AI 对话
  chatCollapsed: { type: Boolean, default: false },
  aiChatSessions: { type: Array, default: () => [] },
  activeAiChatId: { type: [String, Number], default: null },
  chatListRef: { type: Object, default: null },
  chatBtnRefs: { type: Object, required: true },
  chatIndicatorStyle: { type: Object, default: () => ({}) },
  chatIndicatorTransition: { type: Boolean, default: false },
  chatMenuOpenId: { type: [String, Number], default: null },
  renamingChatId: { type: [String, Number], default: null },
  renameText: { type: String, default: '' },
  // 用户菜单
  userMenuOpen: { type: Boolean, default: false },
  // 响应式状态
  sidebarMode: { type: String, default: 'expanded' }, // 'expanded' | 'collapsed-icon'
  isMobile: { type: Boolean, default: false },
  canHover: { type: Boolean, default: true },
  mobileDrawerOpen: { type: Boolean, default: false },
})

const emit = defineEmits([
  'update:currentView', 'update:currentSettingsSubView', 'update:collapsedGroups', 'update:chatCollapsed',
  'update:userMenuOpen', 'update:chatMenuOpenId', 'update:renameText', 'update:renamingChatId',
  'update:navRef', 'update:chatListRef',
  'navigate-home', 'logout', 'toggle-theme',
  'select-project', 'create-project', 'rename-project', 'delete-project',
  'create-ai-chat', 'select-ai-chat',
  'start-rename-chat', 'confirm-rename-chat', 'delete-ai-chat', 'toggle-chat-menu',
  'toggle-sidebar',
])

const isSettingsView = computed(() => props.currentView === 'settings')
const isNarrow = computed(() => !props.isMobile && props.sidebarMode === 'collapsed-icon')
const topNavGroups = computed(() => props.navGroups.filter(group => !group.label))
const lowerNavGroups = computed(() => props.navGroups.filter(group => group.label))
const settingsApiItemIds = new Set(['system-providers', 'api'])
const settingsNavGroups = computed(() => {
  const apiItems = props.settingsNavItems.filter(item => settingsApiItemIds.has(item.id))
  const basicItems = props.settingsNavItems.filter(item => !settingsApiItemIds.has(item.id))
  return [
    { label: null, items: basicItems },
    { label: 'API', items: apiItems },
  ].filter(group => group.items.length)
})
const errorBankProjects = computed(() => props.projects.filter(p => !p.is_default && (p.project_type || 'question') === 'question'))
const noteProjects = computed(() => props.projects.filter(p => !p.is_default && p.project_type === 'note'))
const projectGroupsCollapsed = ref({
  errorBank: false,
  notes: false,
})
const projectMenuOpenId = ref(null)
const projectActiveClass = 'bg-[rgb(var(--accent-rgb)/0.12)] text-[rgb(var(--accent-strong-rgb))] ring-1 ring-[rgb(var(--accent-rgb)/0.18)] dark:bg-[rgb(var(--accent-rgb)/0.10)] dark:text-[rgb(var(--accent-hover-rgb))] dark:ring-[rgb(var(--accent-rgb)/0.14)]'
const projectInactiveClass = 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.035] dark:hover:text-[#d0d6e0]'
const dropdownPanelClass = 'rounded-xl border border-gray-200 bg-white p-1 shadow-xl shadow-black/10 backdrop-blur-md dark:border-white/[0.08] dark:bg-[#1f1f20] dark:shadow-black/35'
const dropdownListClass = 'w-full py-1 text-sm text-gray-700 dark:text-[#d7d7d8]'
const dropdownItemClass = 'flex h-8 cursor-pointer items-center gap-3 rounded-md px-3 outline-none transition-colors hover:bg-gray-100 dark:hover:bg-white/[0.07]'
const dropdownDangerItemClass = 'flex h-8 cursor-pointer items-center gap-3 rounded-md px-3 text-rose-600 outline-none transition-colors hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10'
const dropdownIconClass = 'w-4 shrink-0 text-center text-sm text-gray-500 dark:text-[#9aa0aa]'
const chatScrollReady = ref(!props.chatCollapsed)
const sidebarTransitionPhase = ref(null)
const renderLogoDetails = ref(!isNarrow.value)
const logoDetailsExpanded = ref(!isNarrow.value)
const renderUserDetails = ref(!isNarrow.value)
const userDetailsExpanded = ref(!isNarrow.value)
const useNarrowLayout = computed(() => isNarrow.value && sidebarTransitionPhase.value !== 'collapsing')
const isSidebarWidthTransitioning = computed(() => sidebarTransitionPhase.value !== null)
const renderExpandedContent = computed(() => !useNarrowLayout.value)
const showExpandedProjectSections = computed(() => renderExpandedContent.value)
const expandedContentAlphaClass = computed(() => (
  sidebarTransitionPhase.value === 'collapsing'
    ? 'pointer-events-none opacity-0'
    : 'opacity-100'
))
const preservedChatContentClass = computed(() => (
  isNarrow.value
    ? 'pointer-events-none opacity-0'
    : 'opacity-100'
))
let chatScrollTimer = null
let sidebarTransitionTimer = null
let logoDetailsFallbackTimer = null
let userDetailsFallbackTimer = null
const SIDEBAR_TRANSITION_MS = 1000

const projectDisplayName = (project) => project?.name || ''

const setView = (view) => {
  emit('update:currentView', view)
  if (props.isMobile && props.mobileDrawerOpen) {
    emit('toggle-sidebar')
  }
}

const setSettingsEntry = (subview) => {
  emit('update:currentSettingsSubView', subview)
  if (props.isMobile && props.mobileDrawerOpen) {
    emit('toggle-sidebar')
  }
}

const openSettings = (subview = 'profile') => {
  if (props.currentView !== 'settings') {
    emit('update:currentView', 'settings')
  }
  setSettingsEntry(subview)
}

const returnToApp = () => {
  emit('update:currentView', props.lastWorkspaceView || 'workspace')
  if (props.isMobile && props.mobileDrawerOpen) {
    emit('toggle-sidebar')
  }
}

const selectChat = (s) => {
  projectMenuOpenId.value = null
  emit('update:chatMenuOpenId', null)
  emit('select-ai-chat', s)
  if (props.isMobile && props.mobileDrawerOpen) {
    emit('toggle-sidebar')
  }
}

const createChat = () => {
  emit('create-ai-chat')
  if (props.isMobile && props.mobileDrawerOpen) {
    emit('toggle-sidebar')
  }
}

const selectProjectView = (project, view) => {
  projectMenuOpenId.value = null
  emit('update:chatMenuOpenId', null)
  emit('select-project', project.id)
  setView(view)
}

const renameProject = (project) => {
  projectMenuOpenId.value = null
  emit('update:chatMenuOpenId', null)
  emit('rename-project', project)
}

const deleteProject = (project) => {
  projectMenuOpenId.value = null
  emit('update:chatMenuOpenId', null)
  emit('delete-project', project)
}

const setProjectMenuOpen = (id, open) => {
  projectMenuOpenId.value = open ? id : null
  if (open) emit('update:chatMenuOpenId', null)
}

const setChatMenuOpen = (id, open) => {
  emit('update:chatMenuOpenId', open ? id : null)
  if (open) projectMenuOpenId.value = null
}

const toggleGroup = (gi) => {
  if (isNarrow.value) return // 窄栏模式下不允许折叠分组
  const next = { ...props.collapsedGroups }
  next[gi] = !next[gi]
  emit('update:collapsedGroups', next)
}

const toggleProjectGroup = (key) => {
  if (isNarrow.value) return
  projectMenuOpenId.value = null
  projectGroupsCollapsed.value = {
    ...projectGroupsCollapsed.value,
    [key]: !projectGroupsCollapsed.value[key],
  }
}

const expandProjectGroups = () => {
  projectGroupsCollapsed.value = {
    errorBank: false,
    notes: false,
  }
}

const handleLogoClick = () => {
  if (isNarrow.value) {
    emit('toggle-sidebar')
    return
  }
  emit('navigate-home')
}

const clearLogoDetailsFallback = () => {
  if (!logoDetailsFallbackTimer) return
  window.clearTimeout(logoDetailsFallbackTimer)
  logoDetailsFallbackTimer = null
}

const clearUserDetailsFallback = () => {
  if (!userDetailsFallbackTimer) return
  window.clearTimeout(userDetailsFallbackTimer)
  userDetailsFallbackTimer = null
}

const removeLogoDetailsAfterCollapse = () => {
  clearLogoDetailsFallback()
  if (!isNarrow.value) return
  renderLogoDetails.value = false
}

const removeUserDetailsAfterCollapse = () => {
  clearUserDetailsFallback()
  if (!isNarrow.value) return
  renderUserDetails.value = false
}

const handleLogoDetailsTransitionEnd = (event) => {
  if (event.target !== event.currentTarget) return
  if (event.propertyName !== 'max-width' && event.propertyName !== 'opacity') return
  if (!logoDetailsExpanded.value) {
    removeLogoDetailsAfterCollapse()
  }
}

const handleUserDetailsTransitionEnd = (event) => {
  if (event.target !== event.currentTarget) return
  if (event.propertyName !== 'max-width' && event.propertyName !== 'opacity') return
  if (!userDetailsExpanded.value) {
    removeUserDetailsAfterCollapse()
  }
}

const toggleChatCollapsed = () => {
  projectMenuOpenId.value = null
  emit('update:chatMenuOpenId', null)
  emit('update:chatCollapsed', !props.chatCollapsed)
}

const syncChatScroll = (collapsed) => {
  if (chatScrollTimer) {
    window.clearTimeout(chatScrollTimer)
    chatScrollTimer = null
  }
  chatScrollReady.value = false
  if (!collapsed) {
    chatScrollTimer = window.setTimeout(() => {
      chatScrollReady.value = true
      chatScrollTimer = null
    }, 320)
  }
}

watch(() => props.chatCollapsed, syncChatScroll)

watch(() => props.sidebarMode, (mode, previousMode) => {
  if (props.isMobile || !previousMode || mode === previousMode) return
  if (mode === 'expanded') {
    projectMenuOpenId.value = null
    expandProjectGroups()
  }
  if (sidebarTransitionTimer) window.clearTimeout(sidebarTransitionTimer)
  sidebarTransitionPhase.value = mode === 'collapsed-icon' ? 'collapsing' : 'expanding'
  sidebarTransitionTimer = window.setTimeout(() => {
    sidebarTransitionPhase.value = null
    sidebarTransitionTimer = null
  }, SIDEBAR_TRANSITION_MS)
})

watch(isNarrow, (narrow) => {
  if (narrow) {
    logoDetailsExpanded.value = false
    userDetailsExpanded.value = false
    clearLogoDetailsFallback()
    clearUserDetailsFallback()
    logoDetailsFallbackTimer = window.setTimeout(removeLogoDetailsAfterCollapse, SIDEBAR_TRANSITION_MS + 80)
    userDetailsFallbackTimer = window.setTimeout(removeUserDetailsAfterCollapse, SIDEBAR_TRANSITION_MS + 80)
  } else {
    clearLogoDetailsFallback()
    clearUserDetailsFallback()
    renderLogoDetails.value = true
    renderUserDetails.value = true
    requestAnimationFrame(() => {
      logoDetailsExpanded.value = true
      userDetailsExpanded.value = true
    })
    expandProjectGroups()
  }
})

onBeforeUnmount(() => {
  if (chatScrollTimer) window.clearTimeout(chatScrollTimer)
  if (sidebarTransitionTimer) window.clearTimeout(sidebarTransitionTimer)
  clearLogoDetailsFallback()
  clearUserDetailsFallback()
})

const userDisplayName = computed(() => {
  const user = props.currentUser || {}
  return user.display_name || user.nickname || user.username || '未登录用户'
})

const userInitial = computed(() => {
  const source = userDisplayName.value || props.currentUser?.username || ''
  return source.trim()?.[0]?.toUpperCase() || '?'
})

const userQuota = computed(() => props.currentUser?.quota || null)
const userQuotaSummary = computed(() => {
  const remaining = userQuota.value?.remaining
  const total = userQuota.value?.daily_free_quota
  if (remaining == null || total == null) return ''
  return `今日剩余 ${remaining} / ${total} 点`
})
</script>

<template>
  <!-- ================== 侧边栏容器 ================== -->
  <aside
    class="sidebar-3d-stage flex min-h-0 flex-col z-20 pt-3 transition-[width,transform] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)] will-change-[width,transform]"
    :class="[
      isMobile
        ? 'fixed inset-y-0 left-0 w-64 transform bg-white dark:bg-[#1b1b1d] shadow-2xl ' + (mobileDrawerOpen ? 'translate-x-0' : '-translate-x-full')
        : 'hidden lg:flex lg:fixed lg:left-0 lg:top-0 lg:bottom-0 bg-transparent ' + (isNarrow ? 'w-16' : 'w-64') + (useNarrowLayout ? ' overflow-visible' : ' overflow-hidden')
    ]">

    <!-- 设置视图 -->
    <div class="sidebar-3d-shell relative min-h-0 flex-1">
      <div class="sidebar-3d-card absolute inset-0" :class="isSettingsView ? 'is-flipped' : ''">
        <!-- 设置导航背面：主/设置侧边栏共用同一卡片，通过 3D face 切换而不是重新挂载。 -->
        <div
          class="sidebar-3d-face sidebar-3d-face-back absolute inset-0 flex min-h-0 flex-1 flex-col px-4 py-4 overflow-hidden"
          :class="isSettingsView ? 'sidebar-3d-face-active' : 'sidebar-3d-face-inactive'"
          :aria-hidden="!isSettingsView">
          <button @click="returnToApp"
            class="mb-4 inline-flex w-full items-center gap-2 overflow-hidden px-3 pt-2 text-sm font-medium text-gray-500 transition-all duration-300 ease-[var(--sidebar-transition-timing)] hover:text-gray-700 dark:text-[#8a8f98] dark:hover:text-white">
            <i class="fa-solid fa-arrow-left text-xs"></i>
            <span v-if="renderExpandedContent"
              class="overflow-hidden whitespace-nowrap transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
              :class="useNarrowLayout ? 'max-w-0 -translate-x-1 opacity-0' : 'max-w-[72px] translate-x-0 opacity-100'">返回应用</span>
          </button>

          <nav :ref="(el) => $emit('update:navRef', el)" class="flex flex-col gap-1.5 relative pt-2">
            <div v-if="renderExpandedContent"
              class="overflow-hidden px-3 text-xs font-medium uppercase tracking-[0.15em] text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] dark:text-[#62666d]"
              :class="useNarrowLayout ? 'max-h-0 pt-0 pb-0 opacity-0' : 'max-h-8 pt-2 pb-1 opacity-100'">
              设置
            </div>
            <div class="flex flex-col gap-3">
              <template v-for="(group, groupIndex) in settingsNavGroups" :key="`settings-${groupIndex}`">
                <div class="flex flex-col gap-1.5">
                  <div
                    v-if="group.label && renderExpandedContent"
                    class="overflow-hidden px-3 text-[11px] font-medium uppercase tracking-[0.15em] text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] dark:text-[#62666d]"
                    :class="useNarrowLayout ? 'max-h-0 pt-0 pb-0 opacity-0' : 'max-h-8 pt-2 pb-1 opacity-100'"
                  >
                    {{ group.label }}
                  </div>
                  <template v-for="item in group.items" :key="item.id">
                    <BaseTooltip :text="item.label" :placement="useNarrowLayout ? 'right' : 'bottom'" :disabled="!useNarrowLayout">
                      <button @click="setSettingsEntry(item.id)"
                        class="sidebar-active-fade group relative z-10 flex h-10 items-center rounded-lg border px-3 py-0 text-sm font-medium transition-all duration-300 ease-[var(--sidebar-transition-timing)] w-full"
                        :class="[
                          currentSettingsSubView === item.id
                            ? 'sidebar-active-fade--active text-white shadow-sm border-transparent'
                            : 'border-transparent text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.04] dark:hover:text-[#d0d6e0]',
                          'gap-3'
                        ]">
                        <i class="fa-solid w-4 text-center text-sm" :class="item.icon"></i>
                        <span v-if="renderExpandedContent"
                          class="overflow-hidden whitespace-nowrap transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                          :class="useNarrowLayout ? 'max-w-0 -translate-x-1 opacity-0' : 'max-w-[128px] translate-x-0 opacity-100'">{{
                            item.label }}</span>
                      </button>
                    </BaseTooltip>
                  </template>
                </div>
              </template>
            </div>
          </nav>
        </div>


        <!-- 主视图 -->
        <div
          class="sidebar-3d-face sidebar-3d-face-front absolute inset-0 flex min-h-0 flex-1 flex-col"
          :class="[
            isSettingsView ? 'sidebar-3d-face-inactive' : 'sidebar-3d-face-active',
            useNarrowLayout ? 'overflow-visible' : 'overflow-hidden',
          ]" :aria-hidden="isSettingsView">
          <div class="flex min-h-0 flex-1 flex-col px-4">
          <!-- Logo 标题区 -->
          <div
            class="flex h-14 shrink-0 items-center justify-start transition-[gap] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
            :class="logoDetailsExpanded ? 'gap-2' : 'gap-0'">
            <button @click="handleLogoClick"
              class="flex h-10 w-9 shrink-0 items-center justify-center rounded-md p-1 transition-transform duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
              :class="isNarrow ? 'translate-x-0.5' : 'translate-x-0'"
              :title="canHover ? (isNarrow ? '展开侧边栏' : '返回首页') : null">
              <BaseLogo size="sm" class="shrink-0" />
            </button>
            <div v-if="renderLogoDetails"
              class="flex min-w-0 items-center overflow-hidden transition-[max-width,opacity] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
              :class="logoDetailsExpanded ? 'max-w-[12rem] flex-1 opacity-100' : 'pointer-events-none max-w-0 flex-1 opacity-0'"
              @transitionend="handleLogoDetailsTransitionEnd">
              <span
                class="min-w-0 shrink-0 overflow-hidden whitespace-nowrap text-base font-semibold text-gray-900 dark:text-[#f7f8f8]"
                :class="isSidebarWidthTransitioning ? '' : 'text-ellipsis'">
                智卷错题本
              </span>
              <div class="min-w-0 flex-1"></div>
              <button @click="emit('toggle-sidebar')"
                class="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#62666d] dark:hover:bg-white/[0.04] dark:hover:text-[#8a8f98]"
                :title="canHover ? (isNarrow ? '展开侧边栏' : '收起侧边栏') : null">
                <PanelLeft
                  class="h-4 w-4"
                  :class="!isNarrow ? 'rotate-180' : ''"
                />
              </button>
            </div>
          </div>
          <div class="flex min-h-0 flex-1 flex-col overflow-hidden">
            <!-- 主导航：同一套 DOM 响应展开/折叠，避免两套导航在动画中分离。 -->
            <nav
              v-if="renderExpandedContent || isNarrow"
              :ref="(el) => $emit('update:navRef', el)"
              class="relative flex min-h-0 flex-1 flex-col gap-1.5 pt-3 transition-opacity duration-300 ease-[var(--sidebar-transition-timing)]"
            >

              <template v-for="(group, gi) in topNavGroups" :key="`top-${gi}`">
                <!-- 分组标题（可折叠） -->
                <button v-if="group.label && renderExpandedContent" @click="group.collapsible && toggleGroup(gi)"
                  class="flex items-center gap-1 overflow-hidden text-xs font-medium uppercase tracking-[0.15em] text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] hover:text-gray-700 dark:text-[#62666d] dark:hover:text-[#8a8f98]"
                  :class="[
                    useNarrowLayout ? 'pointer-events-none mt-0 max-h-0 px-3 pb-0 opacity-0' : 'mt-6 max-h-8 px-3 pb-2 opacity-100',
                    group.collapsible ? 'cursor-pointer' : 'cursor-default'
                  ]">
                  <span>{{ group.label }}</span>
                  <i v-if="group.collapsible"
                    class="fa-solid fa-play text-[8px] text-gray-400 dark:text-[#62666d] transition-transform duration-200"
                    :class="collapsedGroups[gi] ? '' : 'rotate-90'"></i>
                </button>

                <!-- 分组内容（grid 折叠动画） -->
                <div class="grid transition-[grid-template-rows] duration-200 ease-out"
                  :class="useNarrowLayout || !collapsedGroups[gi] ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'">
                  <div class="overflow-hidden">
                    <div class="flex flex-col gap-1.5">
                      <template v-for="item in group.items" :key="item.id">
                        <BaseTooltip :text="item.label" placement="right" :disabled="!useNarrowLayout">
                          <button :ref="el => navBtnRefs[item.id] = el"
                            @click="setView(item.id === 'workspace' ? lastWorkspaceView : item.id)"
                            class="sidebar-active-fade group relative z-10 flex items-center rounded-lg text-sm font-medium outline-none transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                            :class="[
                              item.match(currentView)
                                ? 'sidebar-active-fade--active text-white shadow-sm border-none'
                                : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.04] dark:hover:text-[#d0d6e0] transition-colors duration-150',
                              'h-10 w-full gap-3 px-3 py-0'
                            ]">
                            <i class="fa-solid w-4 shrink-0 text-center text-sm" :class="item.icon"></i>
                            <span v-if="renderExpandedContent"
                              class="overflow-hidden whitespace-nowrap transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                              :class="[
                                useNarrowLayout ? 'max-w-0 -translate-x-1 opacity-0' : 'max-w-[128px] translate-x-0 opacity-100',
                                isSidebarWidthTransitioning ? '' : 'text-ellipsis',
                              ]">
                              {{ item.label }}
                            </span>
                          </button>
                        </BaseTooltip>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
              <template v-for="(group, gi) in lowerNavGroups" :key="`lower-${gi}`">
                <!-- 分组标题（可折叠） -->
                <button v-if="group.label && renderExpandedContent" @click="group.collapsible && toggleGroup(gi)"
                  class="flex items-center gap-1 overflow-hidden text-xs font-medium uppercase tracking-[0.15em] text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] hover:text-gray-700 dark:text-[#62666d] dark:hover:text-[#8a8f98]"
                  :class="[
                    useNarrowLayout ? 'pointer-events-none mt-0 max-h-0 px-3 pb-0 opacity-0' : 'mt-6 max-h-8 px-3 pb-2 opacity-100',
                    group.collapsible ? 'cursor-pointer' : 'cursor-default'
                  ]">
                  <span>{{ group.label }}</span>
                  <i v-if="group.collapsible"
                    class="fa-solid fa-play text-[8px] text-gray-400 dark:text-[#62666d] transition-transform duration-200"
                    :class="collapsedGroups[gi] ? '' : 'rotate-90'"></i>
                </button>

                <!-- 分组内容（grid 折叠动画） -->
                <div class="grid transition-[grid-template-rows] duration-200 ease-out"
                  :class="useNarrowLayout || !collapsedGroups[gi] ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'">
                  <div class="overflow-hidden">
                    <div class="flex flex-col gap-1.5">
                      <template v-for="item in group.items" :key="item.id">
                        <BaseTooltip :text="item.label" placement="right" :disabled="!useNarrowLayout">
                          <button :ref="el => navBtnRefs[item.id] = el"
                            @click="setView(item.id === 'workspace' ? lastWorkspaceView : item.id)"
                            class="sidebar-active-fade group relative z-10 flex items-center rounded-lg text-sm font-medium outline-none transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                            :class="[
                              item.match(currentView)
                                ? 'sidebar-active-fade--active text-white shadow-sm border-none'
                                : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.04] dark:hover:text-[#d0d6e0] transition-colors duration-150',
                              'h-10 w-full gap-3 px-3 py-0'
                            ]">
                            <i class="fa-solid w-4 shrink-0 text-center text-sm" :class="item.icon"></i>
                            <span v-if="renderExpandedContent"
                              class="overflow-hidden whitespace-nowrap transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                              :class="[
                                useNarrowLayout ? 'max-w-0 -translate-x-1 opacity-0' : 'max-w-[128px] translate-x-0 opacity-100',
                                isSidebarWidthTransitioning ? '' : 'text-ellipsis',
                              ]">
                              {{ item.label }}
                            </span>
                          </button>
                        </BaseTooltip>
                      </template>
                    </div>
                  </div>
                </div>
              </template>
            <!-- AI 对话历史列表 -->
            <div
              class="mt-2 flex min-h-0 flex-1 flex-col transition-opacity duration-300 ease-[var(--sidebar-transition-timing)]"
              :class="preservedChatContentClass">
            <div
              class="flex items-center justify-between pb-1.5 pl-2.5 pr-0 transition-all duration-300 ease-[var(--sidebar-transition-timing)]">
              <button @click="toggleChatCollapsed"
                class="flex h-6 min-w-0 items-center gap-1.5 overflow-hidden text-xs font-medium text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] hover:text-gray-700 dark:text-[#62666d] dark:hover:text-[#9aa0aa] cursor-pointer"
                :class="useNarrowLayout ? 'pointer-events-none max-w-0 -translate-x-1 opacity-0' : 'max-w-[80px] translate-x-0 opacity-100'">
                <span class="min-w-0 overflow-hidden whitespace-nowrap" :class="isSidebarWidthTransitioning ? '' : 'text-ellipsis'">对话</span>
                <i class="fa-solid fa-play shrink-0 text-[8px] text-gray-400 dark:text-[#62666d] transition-transform duration-200"
                  :class="chatCollapsed ? '' : 'rotate-90'"></i>
              </button>
              <button @click="createChat"
                class="flex items-center justify-center overflow-hidden rounded-md text-gray-500 transition-all duration-300 ease-[var(--sidebar-transition-timing)] hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.04] dark:hover:text-[#d0d6e0]"
                :class="useNarrowLayout ? 'pointer-events-none h-0 w-0 -translate-x-1 opacity-0' : 'h-6 w-6 translate-x-0 opacity-100'"
                :title="canHover ? '新对话' : null">
                <i class="fa-solid fa-plus text-[10px]"></i>
              </button>
            </div>
            <!-- 折叠动画 -->
            <div
              class="grid min-h-0 flex-1 transition-[grid-template-rows] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
              :class="useNarrowLayout || !chatCollapsed ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'">
              <div class="flex min-h-0 flex-col overflow-hidden">
                <div :ref="(el) => $emit('update:chatListRef', el)"
                  class="hidden-scrollbar relative h-full overflow-x-hidden pb-2"
                  :class="chatScrollReady ? 'overflow-y-auto' : 'overflow-y-hidden'"
                  @click="emit('update:chatMenuOpenId', null)">

                  <div v-if="aiChatSessions.length === 0"
                    class="overflow-hidden px-3 text-center text-xs text-gray-400 transition-all duration-300 ease-[var(--sidebar-transition-timing)] dark:text-[#62666d]"
                    :class="useNarrowLayout ? 'max-h-0 py-0 opacity-0' : 'max-h-16 py-4 opacity-100'">
                    暂无对话
                  </div>
                  <div v-for="(s, index) in aiChatSessions" :key="s.id" :ref="el => chatBtnRefs[s.id] = el"
                    class="sidebar-active-fade group relative mb-px flex cursor-pointer items-center rounded-md text-sm font-medium outline-none transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                    :class="[
                      chatMenuOpenId === s.id ? 'z-20' : 'z-10',
                      activeAiChatId === s.id && currentView === 'ai-chat'
                        ? 'sidebar-active-fade--active text-white shadow-sm border-none'
                        : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700 dark:text-[#8a8f98] dark:hover:bg-white/[0.04] dark:hover:text-[#d0d6e0] transition-colors duration-150',
                      'w-full gap-2.5 py-1.5 pl-2.5 pr-0'
                    ]" @click="renamingChatId !== s.id && selectChat(s)">
                    <BaseTooltip :text="s.title" placement="right" :disabled="!useNarrowLayout">
                      <i class="fa-solid fa-message w-3.5 shrink-0 text-center text-xs transition-colors"></i>
                    </BaseTooltip>

                    <div
                      class="flex min-w-0 flex-1 items-center gap-2 overflow-visible transition-all duration-300 ease-[var(--sidebar-transition-timing)]"
                      :class="useNarrowLayout ? 'pointer-events-none max-w-0 -translate-x-1 opacity-0' : 'max-w-none translate-x-0 opacity-100'">
                      <!-- 重命名输入框 -->
                      <input v-if="renamingChatId === s.id" :value="renameText"
                        @input="emit('update:renameText', $event.target.value)" data-rename-input @click.stop
                        @keydown.enter="emit('confirm-rename-chat', s)"
                        @keydown.escape="$emit('update:renamingChatId', null)" @blur="emit('confirm-rename-chat', s)"
                        class="flex-1 min-w-0 bg-transparent text-xs outline-none border-b border-gray-300 py-0.5 text-gray-900 dark:border-white/[0.12] dark:text-[#f7f8f8]" />
                      <span v-else class="relative z-10 flex-1 overflow-hidden whitespace-nowrap" :class="isSidebarWidthTransitioning ? '' : 'text-ellipsis'">{{ s.title }}</span>

                      <BaseDropdown :modelValue="chatMenuOpenId === s.id"
                        @update:modelValue="(open) => setChatMenuOpen(s.id, open)"
                        :position="(aiChatSessions.length > 3 && index >= aiChatSessions.length - 2) ? 'top' : 'bottom'"
                        align="right" width="w-40" wrapperClass="shrink-0"
                        :panelClass="dropdownPanelClass">
                        <template #trigger="{ toggle }">
                          <button
                            class="flex h-6 w-6 items-center justify-center opacity-55 transition-opacity hover:opacity-100"
                            :title="canHover ? '更多操作' : null" @click.stop="toggle">
                            <i class="fa-solid fa-ellipsis text-[9px]"></i>
                          </button>
                        </template>
                        <template #default="{ close }">
                          <ul role="listbox" aria-multiselectable="false" data-checkmark-trailing="true" :class="dropdownListClass">
                            <li role="option" aria-disabled="false" :class="dropdownItemClass" @click.stop="close(); emit('start-rename-chat', s)">
                              <i class="fa-solid fa-pen" :class="dropdownIconClass"></i>
                              <span class="min-w-0 flex-1 truncate font-medium">重命名</span>
                            </li>
                            <li role="separator" class="my-1 h-px bg-gray-200 dark:bg-white/[0.08]"></li>
                            <li role="option" aria-disabled="false" :class="dropdownDangerItemClass" @click.stop="close(); emit('delete-ai-chat', s.id)">
                              <i class="fa-solid fa-trash w-4 shrink-0 text-center text-sm"></i>
                              <span class="min-w-0 flex-1 truncate font-medium">删除</span>
                            </li>
                          </ul>
                        </template>
                      </BaseDropdown>
                    </div>
                  </div>
                </div>
              </div>
            </div>
            </div>
            </nav>
          </div>

          <!-- 底部用户区 -->
          <div
            class="shrink-0"
          >
            <BaseDropdown :modelValue="userMenuOpen" @update:modelValue="(val) => emit('update:userMenuOpen', val)"
              position="top" :align="useNarrowLayout ? 'left' : 'center'"
              :width="useNarrowLayout ? 'w-64' : 'w-full'" offset="mb-1"
              :panelClass="dropdownPanelClass"
              :wrapperClass="useNarrowLayout ? 'inline-block' : 'block w-full'">
              <!-- 背景噪点 -->
              <template #background>
                <div class="ws-bg-noise hidden dark:block"></div>
              </template>

              <template #trigger="{ toggle }">
                <!-- 用户信息 -->
                <button @click.stop="toggle"
                  class="flex h-14 w-full items-center justify-start rounded-md transition-[gap] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
                  :class="userDetailsExpanded ? 'gap-2' : 'gap-0'">
                  <div
                    class="h-7 w-7 shrink-0 rounded-full relative overflow-hidden flex items-center justify-center text-white text-xs font-medium transition-transform duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
                    :class="isNarrow ? 'translate-x-1.5' : 'translate-x-0'"
                    style="background: linear-gradient(to bottom, rgb(var(--accent-rgb) / 0.9), rgb(var(--accent-strong-rgb) / 0.9)); box-shadow: inset 0 1px 0 0 rgba(255,255,255,0.12);">
                    <img v-if="currentUser?.avatar_url" :src="currentUser.avatar_url" alt="用户头像"
                      class="h-full w-full object-cover" />
                    <template v-else>
                      <span class="absolute inset-0 pointer-events-none"
                        style="background-image: linear-gradient(to right, rgba(255,255,255,0.06) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.06) 1px, transparent 1px); background-size: 8px 8px; mask-image: radial-gradient(ellipse at center, black 30%, transparent 80%); -webkit-mask-image: radial-gradient(ellipse at center, black 30%, transparent 80%);"></span>
                      <span class="relative z-10">{{ userInitial }}</span>
                    </template>
                  </div>
                  <div v-if="renderUserDetails"
                    class="flex min-w-0 flex-1 items-center overflow-hidden transition-[max-width,opacity] duration-[var(--sidebar-transition-duration)] ease-[var(--sidebar-transition-timing)]"
                    :class="userDetailsExpanded ? 'max-w-[calc(100%-2.25rem)] opacity-100' : 'pointer-events-none max-w-0 opacity-0'"
                    @transitionend="handleUserDetailsTransitionEnd">
                    <div class="min-w-0 flex-1 overflow-hidden text-left">
                      <p
                        class="truncate text-xs font-medium leading-tight text-gray-900 transition-colors dark:text-[#f7f8f8]">
                        {{
                          userDisplayName }}
                      </p>
                      <p v-if="userQuotaSummary"
                        class="mt-0.5 truncate text-[10px] leading-tight accent-text transition-colors">
                        {{
                          userQuotaSummary }}</p>
                      <p v-else class="truncate text-[10px] leading-tight text-gray-500 transition-colors dark:text-[#62666d]">
                        @{{
                          currentUser?.username || 'guest' }}</p>
                    </div>
                    <i
                      class="fa-solid fa-chevron-up ml-2 w-3 shrink-0 text-[10px] text-gray-400 transition-colors dark:text-[#62666d]"></i>
                  </div>
                </button>
              </template>

              <!-- Dropdown 菜单内容 -->
              <template #default="{ close }">
                <ul
                  role="listbox"
                  aria-multiselectable="false"
                  data-checkmark-trailing="true"
                  :class="dropdownListClass"
                >
                  <li
                    role="option"
                    aria-disabled="false"
                    :class="dropdownItemClass"
                    @click="(e) => { close(); emit('toggle-theme', e.currentTarget) }"
                  >
                    <i class="fa-solid"
                      :class="[dropdownIconClass, isDark ? 'fa-sun' : 'fa-moon']"
                    ></i>
                    <span class="min-w-0 flex-1 truncate font-medium">{{ isDark ? '浅色模式' : '深色模式' }}</span>
                  </li>
                  <li
                    role="option"
                    aria-disabled="false"
                    :class="dropdownItemClass"
                    @click="openSettings('profile'); close()"
                  >
                    <i class="fa-regular fa-circle-user" :class="dropdownIconClass"></i>
                    <span class="min-w-0 flex-1 truncate font-medium">个人资料</span>
                  </li>
                  <li
                    role="option"
                    aria-disabled="false"
                    :class="dropdownItemClass"
                    @click="openSettings('profile'); close()"
                  >
                    <i class="fa-solid fa-gear" :class="dropdownIconClass"></i>
                    <span class="min-w-0 flex-1 truncate font-medium">设置</span>
                  </li>

                  <li role="separator" class="my-1 h-px bg-gray-200 dark:bg-white/[0.08]"></li>

                  <li
                    role="option"
                    aria-disabled="false"
                    :class="dropdownItemClass"
                  >
                    <i class="fa-regular fa-circle-question" :class="dropdownIconClass"></i>
                    <span class="min-w-0 flex-1 truncate font-medium">帮助</span>
                    <i class="fa-solid fa-chevron-right shrink-0 text-[10px] text-gray-400 dark:text-[#7a7f88]"></i>
                  </li>
                  <li
                    role="option"
                    aria-disabled="false"
                    :class="dropdownDangerItemClass"
                    @click="emit('logout'); close()"
                  >
                    <i class="fa-solid fa-right-from-bracket w-4 shrink-0 text-center text-sm"></i>
                    <span class="min-w-0 flex-1 truncate font-medium">退出登录</span>
                  </li>
                </ul>
              </template>
            </BaseDropdown>
          </div>
          </div>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar-active-fade {
  position: relative;
  isolation: isolate;
}

.sidebar-active-fade::before {
  content: '';
  position: absolute;
  inset: 0;
  z-index: -1;
  border-radius: inherit;
  background: linear-gradient(to bottom, rgb(var(--accent-rgb)), rgb(var(--accent-strong-rgb)));
  opacity: 0;
  pointer-events: none;
  transition: opacity 260ms ease;
}

.sidebar-active-fade--active::before {
  opacity: 1;
}

.collapsed-flyout-panel {
  pointer-events: none;
  transform: translateX(-4px) scale(0.98);
  transform-origin: left top;
  transition:
    opacity 140ms ease,
    transform 140ms ease;
}

.collapsed-flyout-trigger:hover .collapsed-flyout-panel,
.collapsed-flyout-trigger:focus-within .collapsed-flyout-panel {
  pointer-events: auto;
  opacity: 1;
  transform: translateX(0) scale(1);
}

.sidebar-3d-stage {
  perspective: 1100px;
  transform-style: preserve-3d;
}

.sidebar-3d-shell {
  perspective: 1100px;
  transform-style: preserve-3d;
}

.sidebar-3d-card {
  transform-style: preserve-3d;
  transform-origin: center center;
  transition: filter 280ms cubic-bezier(0.22, 1, 0.36, 1);
  will-change: filter;
}

.sidebar-3d-card.is-flipped {
  filter: brightness(0.985);
}

.sidebar-3d-face {
  backface-visibility: hidden;
  transform-origin: center center;
  transform-style: preserve-3d;
  transition:
    opacity 120ms ease,
    transform 280ms cubic-bezier(0.22, 1, 0.36, 1),
    filter 280ms cubic-bezier(0.22, 1, 0.36, 1);
  will-change: opacity, transform, filter;
}

.sidebar-3d-face-front {
  transform: rotateY(0deg) translateX(0) scale(1);
}

.sidebar-3d-face-back {
  transform: rotateY(0deg) translateX(0) scale(1);
}

.sidebar-3d-face-front.sidebar-3d-face-active,
.sidebar-3d-face-back.sidebar-3d-face-active {
  opacity: 1;
  filter: brightness(1);
  pointer-events: auto;
  transform: rotateY(0deg) translateX(0) scale(1);
}

.sidebar-3d-face-front.sidebar-3d-face-inactive {
  opacity: 0;
  filter: brightness(0.9);
  pointer-events: none;
  transform: rotateY(90deg) scale(0.98);
}

.sidebar-3d-face-back.sidebar-3d-face-inactive {
  opacity: 0;
  filter: brightness(0.9);
  pointer-events: none;
  transform: rotateY(-90deg) scale(0.98);
}

.hidden-scrollbar {
  scrollbar-width: none;
  -ms-overflow-style: none;
}

.hidden-scrollbar::-webkit-scrollbar {
  display: none;
}

.custom-scrollbar::-webkit-scrollbar {
  width: 4px;
  height: 4px;
}

.custom-scrollbar::-webkit-scrollbar-button {
  display: none;
  width: 0;
  height: 0;
}

.custom-scrollbar::-webkit-scrollbar-track {
  background: transparent;
}

.custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(0, 0, 0, 0.1);
  border-radius: 999px;
}

.dark .custom-scrollbar::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.05);
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>

