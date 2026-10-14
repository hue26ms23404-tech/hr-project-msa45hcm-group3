import React, { useEffect, useState } from 'react';
import { Layout, Menu, Button, Avatar, Dropdown, Space, Tag } from 'antd';
import {
  DashboardOutlined,
  TeamOutlined,
  QrcodeOutlined,
  ClockCircleOutlined,
  CalendarOutlined,
  DollarOutlined,
  LogoutOutlined,
  UserOutlined,
  RobotOutlined,
  ThunderboltOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';
import { useAuth } from '../context/AuthContext';
import { AIChatModal } from './AIChatModal';

const { Header, Sider, Content } = Layout;

export const MainLayout: React.FC = () => {
  const { user, logout, hasRole } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const [aiModalOpen, setAiModalOpen] = useState(false);

  useEffect(() => {
    if (location.state?.openAI) {
      setAiModalOpen(true);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location.state, location.pathname, navigate]);

  const menuItems = [
    {
      key: '/dashboard',
      icon: <DashboardOutlined />,
      label: 'Tổng quan',
    },
    {
      key: '/attendance',
      icon: <ClockCircleOutlined />,
      label: 'Chấm công',
    },
    {
      key: '/leaves',
      icon: <CalendarOutlined />,
      label: 'Nghỉ phép',
    },
    {
      key: '/payroll',
      icon: <DollarOutlined />,
      label: 'Bảng lương',
    },
    {
      key: '/kiosk',
      icon: <QrcodeOutlined />,
      label: 'Màn hình Kiosk',
    },
    ...(hasRole(['ADMIN', 'HR']) ? [{ key: '/knowledge', icon: <RobotOutlined />, label: 'Tài liệu & chính sách' }] : []),
    ...(hasRole(['ADMIN']) ? [{ key: '/ai-access', icon: <SafetyCertificateOutlined />, label: 'Quyền truy cập AI' }] : []),
    ...(hasRole(['ADMIN', 'HR', 'MANAGER'])
      ? [
          {
            key: '/employees',
            icon: <TeamOutlined />,
            label: hasRole(['ADMIN', 'HR']) ? 'Nhân sự & QR Card' : 'Nhân viên của tôi',
          },
        ]
      : []),
  ];

  const userMenu = {
    items: [
      {
        key: 'user-info',
        disabled: true,
        label: (
          <div style={{ padding: '4px 0' }}>
            <div style={{ fontWeight: 600 }}>{user?.login_email}</div>
            <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
              {user?.roles.map((r) => (
                <Tag color={r === 'ADMIN' ? 'red' : r === 'MANAGER' ? 'orange' : 'blue'} key={r}>
                  {r}
                </Tag>
              ))}
            </div>
          </div>
        ),
      },
      {
        type: 'divider' as const,
      },
      {
        key: 'logout',
        icon: <LogoutOutlined />,
        danger: true,
        label: 'Đăng xuất',
        onClick: logout,
      },
    ],
  };

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        collapsible
        collapsed={collapsed}
        onCollapse={(val) => setCollapsed(val)}
        theme="light"
        width={240}
        style={{
          borderRight: '1px solid #e2e8f0',
          position: 'sticky',
          top: 0,
          height: '100vh',
          zIndex: 100,
        }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            padding: '0 20px',
            gap: 12,
            borderBottom: '1px solid #f1f5f9',
          }}
        >
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: 8,
              background: 'linear-gradient(135deg, #1677ff 0%, #0050b3 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#fff',
              fontWeight: 800,
              fontSize: 18,
            }}
          >
            HR
          </div>
          {!collapsed && (
            <div style={{ overflow: 'hidden', whiteSpace: 'nowrap' }}>
              <div style={{ fontWeight: 700, fontSize: 15, color: '#0f172a' }}>Group 3 HRMS</div>
              <div style={{ fontSize: 11, color: '#64748b' }}>AI-Powered System</div>
            </div>
          )}
        </div>

        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
          style={{ borderRight: 'none', marginTop: 12, fontWeight: 500 }}
        />

        {/* AI Quick helper promo inside sider */}
        {!collapsed && (
          <div
            style={{
              margin: '24px 16px',
              padding: 14,
              borderRadius: 12,
              background: 'linear-gradient(135deg, #eff6ff 0%, #e0e7ff 100%)',
              border: '1px solid #bfdbfe',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <RobotOutlined style={{ color: '#2563eb', fontSize: 16 }} />
              <span style={{ fontWeight: 600, fontSize: 13, color: '#1e3a8a' }}>AI Hỗ trợ</span>
            </div>
            <p style={{ fontSize: 11, color: '#3b82f6', marginBottom: 10, lineHeight: 1.4 }}>
              Hỏi chính sách, tra cứu công hoặc tạo đơn chỉ trong 5 giây!
            </p>
            <Button
              type="primary"
              size="small"
              icon={<ThunderboltOutlined />}
              block
              onClick={() => setAiModalOpen(true)}
              style={{
                borderRadius: 6,
                background: '#2563eb',
                fontSize: 12,
                fontWeight: 600,
              }}
            >
              Mở Trợ lý AI
            </Button>
          </div>
        )}
      </Sider>

      <Layout>
        <Header
          style={{
            height: 64,
            padding: '0 24px',
            background: '#ffffff',
            borderBottom: '1px solid #e2e8f0',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            position: 'sticky',
            top: 0,
            zIndex: 90,
          }}
        >
          {/* Office hours badge */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Tag color="cyan" style={{ padding: '4px 10px', fontSize: 12, borderRadius: 6, border: 'none' }}>
              🕒 Giờ chuẩn: <b>08:00 - 17:00</b> (Nghỉ trưa 12:00 - 13:00 • 8h/ngày)
            </Tag>
          </div>

          {/* Right Header items */}
          <Space orientation="horizontal" size="middle">
            <Button
              type="default"
              icon={<RobotOutlined style={{ color: '#1677ff' }} />}
              onClick={() => setAiModalOpen(true)}
              style={{
                borderRadius: 8,
                borderColor: '#93c5fd',
                backgroundColor: '#f0f7ff',
                color: '#1d4ed8',
                fontWeight: 600,
              }}
            >
              Hỏi AI Assistant
            </Button>

            <Dropdown menu={userMenu} placement="bottomRight" arrow>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  cursor: 'pointer',
                  padding: '4px 8px',
                  borderRadius: 8,
                  transition: 'background 0.2s',
                }}
              >
                <Avatar style={{ backgroundColor: '#1677ff' }} icon={<UserOutlined />} />
                <div style={{ display: 'flex', flexDirection: 'column', textAlign: 'left', lineHeight: 1.4, maxWidth: 180, minWidth: 0 }}>
                  <span title={user?.login_email} style={{ fontSize: 13, fontWeight: 600, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user?.login_email.split('@')[0]}
                  </span>
                  <span style={{ fontSize: 11, color: '#64748b', lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {user?.roles.join(', ')}
                  </span>
                </div>
              </div>
            </Dropdown>
          </Space>
        </Header>

        <Content
          style={{
            margin: 24,
            minHeight: 'calc(100vh - 112px)',
          }}
        >
          <Outlet />
        </Content>
      </Layout>

      {/* Keep the table toolbar clear; the header still opens the assistant. */}
      {!aiModalOpen && !location.pathname.startsWith("/reports") && <div
        style={{
          position: 'fixed',
          bottom: 28,
          right: 28,
          zIndex: 999,
        }}
      >
        <Button
          type="primary"
          shape="circle"
          size="large"
          icon={<RobotOutlined style={{ fontSize: 24 }} />}
          onClick={() => setAiModalOpen(true)}
          style={{
            width: 56,
            height: 56,
            boxShadow: '0 4px 14px rgba(22, 119, 255, 0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        />
      </div>}

      <AIChatModal key={user?.user_account_id ?? 'anonymous'} open={aiModalOpen} onClose={() => setAiModalOpen(false)} />
    </Layout>
  );
};
