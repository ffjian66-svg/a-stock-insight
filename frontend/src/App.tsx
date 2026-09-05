import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DashboardPage } from './pages/DashboardPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { SettingsPage } from './pages/SettingsPage'
import { StockDetailPage } from './pages/StockDetailPage'

const client=new QueryClient({defaultOptions:{queries:{staleTime:15_000,retry:1}}})
const router=createBrowserRouter([{element:<Layout/>,children:[{path:'/',element:<DashboardPage/>},{path:'/screener',element:<ScreenerPage/>},{path:'/stock/:code',element:<StockDetailPage/>},{path:'/settings',element:<SettingsPage/>}]}])
export default function App(){return <QueryClientProvider client={client}><RouterProvider router={router}/></QueryClientProvider>}
