import { Outlet } from 'react-router-dom';
import {
  SidebarProvider,
  SidebarInset,
  SidebarTrigger,
} from 'components/ui/sidebar';
import { Separator } from 'components/ui/separator';
import { AppSidebar } from 'components/AppSidebar';
import ActivityLogPanel from 'components/ActivityLogPanel';

const MainLayout = () => {
  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset>
        <header className="flex h-16 shrink-0 items-center gap-2 border-b px-4 sticky top-0 z-20 bg-background">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
        </header>
        <div className="flex-1 overflow-auto p-6 md:p-8">
          <Outlet />
        </div>
      </SidebarInset>
      <ActivityLogPanel />
    </SidebarProvider>
  );
};

export default MainLayout;
