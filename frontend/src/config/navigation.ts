export type AppNavIcon =
  | 'overview'
  | 'tasks'
  | 'chat'
  | 'schedule'
  | 'settings'

export interface AppNavItem {
  id: string
  name: string
  path: string
  icon: AppNavIcon
}

export const appNavigation: AppNavItem[] = [
  { id: 'overview', name: '总览', path: '/overview', icon: 'overview' },
  { id: 'tasks', name: 'AI分析', path: '/tasks', icon: 'tasks' },
  { id: 'chat', name: 'AI聊天', path: '/chat', icon: 'chat' },
  { id: 'schedule', name: '定时设置', path: '/schedule', icon: 'schedule' },
  { id: 'settings', name: '功能设置', path: '/settings', icon: 'settings' },
]

export function navTitleForPath(path: string): string {
  const item = appNavigation.find(
    (nav) => path === nav.path || path.startsWith(nav.path + '/'),
  )
  return item?.name ?? 'Aniu'
}
