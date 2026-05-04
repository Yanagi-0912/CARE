import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import Home from '../pages/Home';

// 1. 模擬 (Mock) react-router-dom 的 useNavigate
const mockNavigate = vi.fn();

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe('Home Page Component', () => {
  // 每次測試前清除 mock 的紀錄，確保測試間互相獨立
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  const renderHome = () => {
    return render(
      <BrowserRouter>
        <Home />
      </BrowserRouter>
    );
  };

  it('應該要正確渲染標題與歡迎詞', () => {
    renderHome();
    
    // 檢查實際渲染的標題
    expect(screen.getByRole('heading', { name: 'CARE 健康管家', level: 1 })).toBeInTheDocument();
    
    // 檢查實際渲染的副標題
    expect(screen.getByText('點選下方卡片開始管理您的健康')).toBeInTheDocument();
  });

  it('應該要渲染出所有的功能卡片', () => {
    renderHome();
    
    // 檢查四個卡片的標題是否符合實際文字
    expect(screen.getByText('個人健康')).toBeInTheDocument();
    expect(screen.getByText('家庭介面')).toBeInTheDocument();
    expect(screen.getByText('設定頁面')).toBeInTheDocument();
    expect(screen.getByText('帳號登入')).toBeInTheDocument();

    // 檢查描述文字是否符合實際文字
    expect(screen.getByText('紀錄與預約')).toBeInTheDocument();
    expect(screen.getByText('管理家人狀況')).toBeInTheDocument();
  });

  it('點擊卡片時，應該觸發 navigate 並前往對應的路徑', () => {
    renderHome();

    // 測試點擊「個人健康」
    const healthButton = screen.getByText('個人健康').closest('button');
    fireEvent.click(healthButton!);
    expect(mockNavigate).toHaveBeenCalledWith('/personalhealth');

    // 測試點擊「家庭介面」
    const familyButton = screen.getByText('家庭介面').closest('button');
    fireEvent.click(familyButton!);
    expect(mockNavigate).toHaveBeenCalledWith('/family');

    // 測試點擊「設定頁面」
    const settingsButton = screen.getByText('設定頁面').closest('button');
    fireEvent.click(settingsButton!);
    expect(mockNavigate).toHaveBeenCalledWith('/settings');

    // 測試點擊「帳號登入」
    const loginButton = screen.getByText('帳號登入').closest('button');
    fireEvent.click(loginButton!);
    expect(mockNavigate).toHaveBeenCalledWith('/login');
  });
});