import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import { Layout } from './components/Layout'
import { DashboardPage } from './pages/DashboardPage'
import { QuantPage } from './pages/QuantPage'
import { ScreenerPage } from './pages/ScreenerPage'
import { SettingsPage } from './pages/SettingsPage'
import { StockDetailPage } from './pages/StockDetailPage'
import { StrategyPage } from './pages/StrategyPage'
import { TomorrowPage } from './pages/TomorrowPage'

const client=new QueryClient({defaultOptions:{queries:{staleTime:15_000,retry:1}}})
const router=createBrowserRouter([{element:<Layout/>,children:[{path:'/',element:<DashboardPage/>},{path:'/tomorrow',element:<TomorrowPage/>},{path:'/screener',element:<ScreenerPage/>},{path:'/quant',element:<QuantPage/>},{path:'/strategy',element:<StrategyPage/>},{path:'/stock/:code',element:<StockDetailPage/>},{path:'/settings',element:<SettingsPage/>}]}])
export default function App(){return <QueryClientProvider client={client}><RouterProvider router={router}/></QueryClientProvider>}
