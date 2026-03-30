import { useLocation, useNavigate, Link } from 'react-router-dom';
import {
  LayoutDashboard,
  Building2,
  BookOpen,
  Cpu,
  AppWindow,
  Package,
  GraduationCap,
  KeyRound,
  Trash2,
  Sun,
  Moon,
  LogOut,
  ChevronsUpDown,
  User,
  BookOpenCheck,
} from 'lucide-react';

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from 'components/ui/sidebar';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from 'components/ui/dropdown-menu';
import { Separator } from 'components/ui/separator';
import { useThemeStore } from 'stores/theme';
import { useAuthStore } from 'stores/auth';

const navItems = [
  { label: 'Dashboard', icon: LayoutDashboard, path: '/dashboard' },
  { label: 'Publishers', icon: Building2, path: '/publishers' },
  { label: 'All Books', icon: BookOpen, path: '/books' },
  { label: 'AI Processing', icon: Cpu, path: '/processing' },
  { label: 'Applications', icon: AppWindow, path: '/apps' },
  { label: 'Bundles', icon: Package, path: '/bundles' },
  { label: 'Teachers', icon: GraduationCap, path: '/teachers' },
  { label: 'API Keys', icon: KeyRound, path: '/api-keys' },
  { label: 'Trash', icon: Trash2, path: '/trash' },
];

export function AppSidebar() {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useThemeStore();
  const logout = useAuthStore((s) => s.logout);
  const { state } = useSidebar();
  const isCollapsed = state === 'collapsed';

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const isActive = (path: string) => {
    if (path === '/dashboard')
      return location.pathname === '/dashboard' || location.pathname === '/';
    return location.pathname.startsWith(path);
  };

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="p-4">
        <Link
          to="/dashboard"
          className="flex items-center gap-2 text-sidebar-foreground hover:text-sidebar-foreground/80 transition-colors"
        >
          <BookOpenCheck className="h-6 w-6 shrink-0 text-primary" />
          {!isCollapsed && (
            <span className="text-base font-semibold truncate">
              Flow Central Storage
            </span>
          )}
        </Link>
      </SidebarHeader>

      <Separator className="mx-2" />

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel className="sr-only">Navigation</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {navItems.map((item) => (
                <SidebarMenuItem key={item.path}>
                  <SidebarMenuButton
                    asChild
                    isActive={isActive(item.path)}
                    tooltip={item.label}
                  >
                    <Link to={item.path}>
                      <item.icon />
                      <span>{item.label}</span>
                    </Link>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <SidebarMenu>
          <SidebarMenuItem>
            <SidebarMenuButton
              onClick={toggleMode}
              tooltip={mode === 'light' ? 'Dark mode' : 'Light mode'}
            >
              {mode === 'light' ? (
                <Moon className="h-4 w-4" />
              ) : (
                <Sun className="h-4 w-4" />
              )}
              <span>{mode === 'light' ? 'Dark Mode' : 'Light Mode'}</span>
            </SidebarMenuButton>
          </SidebarMenuItem>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <SidebarMenuButton
                  size="lg"
                  tooltip="Account"
                  className="data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground"
                >
                  <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary text-primary-foreground shrink-0">
                    <User className="h-4 w-4" />
                  </div>
                  <div className="grid flex-1 text-left text-sm leading-tight">
                    <span className="truncate font-semibold">Admin</span>
                    <span className="truncate text-xs text-muted-foreground">
                      Administrator
                    </span>
                  </div>
                  <ChevronsUpDown className="ml-auto size-4" />
                </SidebarMenuButton>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                className="w-[--radix-dropdown-menu-trigger-width] min-w-56 rounded-lg"
                side="top"
                align="start"
                sideOffset={4}
              >
                <DropdownMenuLabel className="text-xs text-muted-foreground">
                  Account
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={handleLogout}>
                  <LogOut className="h-4 w-4" />
                  Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarFooter>
    </Sidebar>
  );
}

export default AppSidebar;
