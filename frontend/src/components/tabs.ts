// The app's top-level tabs. Add a route here and it appears on every page: the shared
// AppHeader and Chat's own sidebar both read this list.
// ``ownerOnly``: her insides (the backend refuses everyone else); use ``tabsFor`` to show only what this user may open.
export const TABS: { to: string; label: string; ownerOnly?: boolean }[] = [
  { to: '/chat', label: 'Chat' },
  { to: '/predict', label: 'Scan Meal' },
  { to: '/system', label: 'System', ownerOnly: true },
  { to: '/integrations', label: 'Integrations' },
]

export function tabsFor(user: { is_owner?: boolean } | null) {
  return TABS.filter((tab) => !tab.ownerOnly || user?.is_owner)
}
