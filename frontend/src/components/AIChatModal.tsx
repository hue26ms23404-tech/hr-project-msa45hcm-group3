import React, { useState, useRef, useEffect } from 'react';
import { Modal, Input, Button, Dropdown, Card, Spin, Space, Typography, message } from 'antd';
import { SendOutlined, RobotOutlined, UserOutlined, ThunderboltOutlined } from '@ant-design/icons';
import api from '../api/client';
import { acceptsServerSuggestions, actorChanged, chatFailureState, readableISODate } from '../api/aiChatState';
import { ChatReportPreview } from './ChatReportPreview';
import { Link, useNavigate } from 'react-router-dom';
import type { AIChatAction, AIChatResponse, AISource, AISuggestion, AIInputOption, AIDraftInputs, AIChatRequest } from '../types/ai';
import { useAuth } from '../context/AuthContext';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  action?: AIChatAction;
  timestamp: string;
  sources?: AISource[];
}

interface AIChatModalProps {
  open: boolean;
  onClose: () => void;
}

export const AIChatModal: React.FC<AIChatModalProps> = ({ open, onClose }) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const actorId = user?.user_account_id;
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content: 'Xin chào! Tôi là Trợ lý AI Nhân sự Group 3. Bạn có thể hỏi tôi về quy chế chấm công, số dư ngày phép, hoặc yêu cầu hỗ trợ tạo đơn nghỉ phép/giải trình.',
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    },
  ]);
  const [previewRunId, setPreviewRunId] = useState<string | null>(null);
  const [editingRunId, setEditingRunId] = useState<string | null>(null);
  const [lastReportRunId, setLastReportRunId] = useState<string | null>(null);
  const [inputVal, setInputVal] = useState('');
  const [loading, setLoading] = useState(false);
  const [activeDraftId, setActiveDraftId] = useState<string | null>(null);
  const [activeDraftRevision, setActiveDraftRevision] = useState<number | null>(null);
  const [missingFields, setMissingFields] = useState<string[]>([]);
  const [inputOptions, setInputOptions] = useState<AIInputOption[]>([]);
  const [suggestions, setSuggestions] = useState<AISuggestion[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [suggestionsError, setSuggestionsError] = useState(false);
  const [suggestionsRetry, setSuggestionsRetry] = useState(0);
  const [retryMessage, setRetryMessage] = useState<{ text: string; startNew: boolean; suggestionId?: string; inputs?: AIDraftInputs } | null>(null);
  const [reference, setReference] = useState<{ title: string; content: string; source_url?: string | null } | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const actorIdRef = useRef(actorId);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    if (open) {
      setTimeout(scrollToBottom, 150);
    }
  }, [open, messages]);

  useEffect(() => {
    if (actorChanged(actorIdRef.current, actorId)) {
      actorIdRef.current = actorId;
      setMessages([]);
      setActiveDraftId(null);
      setActiveDraftRevision(null);
      setMissingFields([]);
      setInputOptions([]);
      setLastReportRunId(null);
      setEditingRunId(null);
      setPreviewRunId(null);
      setRetryMessage(null);
    }
  }, [actorId]);

  useEffect(() => {
    if (!open || actorId == null) return;
    let cancelled = false;
    setSuggestions([]);
    setSuggestionsError(false);
    setSuggestionsLoading(true);
    api.get<AISuggestion[]>('/ai/suggestions').then((res) => {
      if (!cancelled && acceptsServerSuggestions(res.data)) setSuggestions(res.data);
      else if (!cancelled) setSuggestionsError(true);
    }).catch(() => {
      if (!cancelled) setSuggestionsError(true);
    }).finally(() => {
      if (!cancelled) setSuggestionsLoading(false);
    });
    return () => { cancelled = true; };
  }, [open, actorId, suggestionsRetry]);

  const handleSendMessage = async (textToSend?: string, startNew = false, suggestionId?: string, inputs?: AIDraftInputs) => {
    const text = textToSend || inputVal.trim();
    if (!text || loading) return;
    setRetryMessage(null);

    const userMsg: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev.map((item) => ({ ...item, action: undefined })), userMsg]);
    setInputVal('');
    setLoading(true);
    const requestActorId = actorId;

    try {
      const history = messages.slice(-6).map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const request: AIChatRequest = {
        message: text,
        ...((editingRunId || lastReportRunId && /báo cáo (này|vừa)|bao cao (nay|vua)|this report/i.test(text)) && !startNew && !activeDraftId
          ? { report_run_id: editingRunId || lastReportRunId! } : {}),
        conversation_history: history,
        ...(suggestionId ? { suggestion_id: suggestionId } : {}),
        ...(inputs && activeDraftId && !startNew ? { inputs } : {}),
        ...(activeDraftId && !startNew ? { draft_id: activeDraftId, ...(activeDraftRevision ? { draft_revision: activeDraftRevision } : {}) } : {}),
      };
      const res = await api.post<AIChatResponse>('/ai/chat', request);
      if (actorIdRef.current !== requestActorId) return;
      if (res.data.action?.action_type === 'OPEN_REPORT' && res.data.action.data.run_id) {
        setLastReportRunId(res.data.action.data.run_id);
        setPreviewRunId(res.data.action.data.run_id); setEditingRunId(null);
      } else if (res.data.draft_id || startNew) setEditingRunId(null);
      setActiveDraftId(res.data.draft_id || null);
      setActiveDraftRevision(res.data.draft_revision || null);
      setMissingFields(res.data.missing_fields || []);
      setInputOptions(res.data.input_options || []);
      if (res.data.message_code === 'AI_UNAVAILABLE') {
        setRetryMessage({ text, startNew, suggestionId, inputs });
      }

      const aiMsg: ChatMessage = {
        id: (Date.now() + 1).toString(),
        role: 'assistant',
        content: res.data.reply,
        action: res.data.action || undefined,
        sources: res.data.sources,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      };

      setMessages((prev) => [...prev, aiMsg]);
    } catch (err: any) {
      if (actorIdRef.current !== requestActorId) return;
      const status = err?.response?.status;
      const code = err?.response?.data?.detail;
      const failure = chatFailureState(status, code);
      if (failure.clearDraft) { setActiveDraftId(null); setActiveDraftRevision(null); setMissingFields([]); }
      if (failure.retryable) setRetryMessage({ text, startNew, suggestionId, inputs });
      const errorMessage = typeof err?.response?.data?.message === 'string' ? err.response.data.message : status === 422
        ? 'Thông tin chưa hợp lệ. Vui lòng kiểm tra các trường được yêu cầu.'
        : status === 403 || code === 'AI_DRAFT_NOT_FOUND'
        ? 'Bạn không có quyền thực hiện yêu cầu này hoặc truy cập dữ liệu được yêu cầu. Vui lòng chọn chức năng trong phạm vi quyền của bạn.'
        : status === 410 && code === 'AI_DRAFT_EXPIRED'
          ? 'Bản nháp đã hết hạn. Vui lòng bắt đầu lại từ một gợi ý có sẵn.'
        : status === 409
          ? 'Bản nháp đã thay đổi hoặc kết thúc. Vui lòng bắt đầu lại từ một gợi ý có sẵn.'
        : status === 401
          ? 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại để tiếp tục.'
        : status === 429
          ? 'Bạn đã gửi quá nhiều yêu cầu. Vui lòng đợi một phút rồi thử lại.'
        : code === 'DATA_SERVICE_UNAVAILABLE'
          ? 'Hiện không thể truy xuất dữ liệu HR. Vui lòng thử lại sau. Báo cáo chưa được tạo.'
        : code === 'REPORT_UNAVAILABLE'
          ? 'Kỳ lương chưa được phát hành hoặc báo cáo không còn khả dụng.'
        : code === 'REPORT_TOO_LARGE'
          ? 'Báo cáo vượt giới hạn 10.000 dòng. Vui lòng thu hẹp kỳ hoặc phòng ban.'
        : code === 'AI_UNAVAILABLE'
          ? 'Hiện không thể kết nối dịch vụ AI để xử lý yêu cầu này. Bạn có thể chọn một gợi ý có sẵn hoặc thử lại sau.'
          : 'Không thể kết nối hệ thống HR. Vui lòng kiểm tra kết nối và thử lại. Báo cáo và dữ liệu cá nhân cần kết nối hệ thống.';
      setMessages((prev) => [
        ...prev,
        {
          id: (Date.now() + 1).toString(),
          role: 'assistant',
          content: errorMessage,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleActionClick = (action: AIChatAction) => {
    if (action.action_type === 'OPEN_REPORT') {
      if (action.data.run_id) setPreviewRunId(action.data.run_id);
      else void handleSendMessage('Tạo báo cáo', true, undefined);
      return;
    }
    onClose();
    if (action.action_type === 'DRAFT_LEAVE' || action.action_type === 'NAVIGATE') {
      const path = action.action_type === 'NAVIGATE' && ['/leaves', '/attendance', '/reports', '/knowledge'].includes(action.data?.path) ? action.data.path : '/leaves';
      navigate(path, { state: action.action_type === 'DRAFT_LEAVE' ? { aiDraft: action.data } : undefined });
    } else if (action.action_type === 'DRAFT_FIX') {
      navigate('/attendance', { state: { aiDraft: action.data } });
    } else if (action.action_type === 'SHOW_DATA') {
      navigate(action.data?.type === 'LEAVE_BALANCE' ? '/leaves' : '/attendance');
    } else if (action.action_type === 'OPEN_APPROVALS') {
      navigate(action.data?.target === 'attendance' ? '/attendance' : '/leaves', { state: { aiTab: 'approvals' } });

    } else if (action.action_type === 'OPEN_KNOWLEDGE') {
      navigate('/knowledge');
    } else if (action.action_type === 'OPEN_SOURCE') {
      navigate(`/knowledge/view/${action.data.document_id}?version=${action.data.version_id}&section=${action.data.section_id}`);
    }
  };

  const readReference = async (documentId: number) => {
    try {
      const response = await api.get(`/ai/knowledge/${documentId}`);
      setReference(response.data);
    } catch { message.error('Tài liệu không còn khả dụng hoặc bạn không có quyền truy cập.'); }
  };

  return (
    <Modal
      open={open}
      centered
      onCancel={onClose}
      footer={null}
      title={
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div
            style={{
              width: 34,
              height: 34,
              borderRadius: 8,
              backgroundColor: '#e6f4ff',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: '#1677ff',
              fontSize: 18,
            }}
          >
            <RobotOutlined />
          </div>
          <div>
            <div style={{ fontWeight: 600, fontSize: 16 }}>HR AI Assistant</div>
            <div style={{ fontSize: 12, color: '#64748b' }}>Tra cứu có nguồn & hỗ trợ soạn đơn</div>
          </div>
        </div>
      }
      width={680}
      styles={{
        body: { padding: '16px 20px', display: 'flex', flexDirection: 'column', height: 'min(620px, calc(100dvh - 160px))', minHeight: 0 },
      }}
    >
      {/* Messages list */}
      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          paddingRight: 8,
          display: 'flex',
          flexDirection: 'column',
          gap: 16,
        }}
      >
        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              display: 'flex',
              flexDirection: msg.role === 'user' ? 'row-reverse' : 'row',
              alignItems: 'flex-start',
              gap: 10,
            }}
          >
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: '50%',
                backgroundColor: msg.role === 'user' ? '#1677ff' : '#f0f5ff',
                color: msg.role === 'user' ? '#fff' : '#1677ff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: 14,
                flexShrink: 0,
              }}
            >
              {msg.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
            </div>

            <div style={{ maxWidth: '82%', minWidth: 0 }}>
              <div
                style={{
                  backgroundColor: msg.role === 'user' ? '#1677ff' : '#ffffff',
                  color: msg.role === 'user' ? '#ffffff' : '#0f172a',
                  padding: '12px 16px',
                  borderRadius: msg.role === 'user' ? '16px 4px 16px 16px' : '4px 16px 16px 16px',
                  boxShadow: msg.role === 'user' ? '0 2px 8px rgba(22, 119, 255, 0.2)' : '0 2px 8px rgba(0,0,0,0.06)',
                  border: msg.role === 'user' ? 'none' : '1px solid #e2e8f0',
                  fontSize: 14,
                  lineHeight: 1.6,
                  whiteSpace: 'pre-wrap',
                  overflowWrap: 'anywhere',
                }}
              >
                {msg.content}
                {!!msg.sources?.length && <div style={{ marginTop: 12 }}>
                  <Typography.Text strong>Tài liệu tham khảo</Typography.Text>
                  {msg.sources.map((source, index) => <div key={`${source.document_id}-${source.section_code || index}`}>
                    {source.viewer_path ? <Link to={source.viewer_path} onClick={onClose} style={{ display: 'block', padding: '6px 0', color: '#1677ff' }}>
                      [{index + 1}] {source.title}<br />
                      <span style={{ fontSize: 12 }}>Xem đúng mục: {source.heading || source.section_code} · Trang {source.page_start}{source.page_end !== source.page_start ? `–${source.page_end}` : ''}</span>
                    </Link> : <Button type="link" style={{ padding: 0, whiteSpace: 'normal', height: 'auto' }} onClick={() => void readReference(source.document_id)}>[{index + 1}] {source.title} · Xem nội dung nguồn</Button>}
                  </div>)}
                </div>}

                {/* Render Suggested Action if available */}
                {msg.action && (
                  <div style={{ marginTop: 12, paddingTop: 10, borderTop: '1px dashed #cbd5e1' }}>
                    {(msg.action.action_type === 'DRAFT_LEAVE' || msg.action.action_type === 'DRAFT_FIX') ? <Card
                      size="small" style={{ backgroundColor: '#f8fafc', borderColor: '#dbeafe' }}
                      title={msg.action.action_type === 'DRAFT_LEAVE' ? 'Nháp đơn nghỉ phép' : 'Nháp giải trình công'}
                    >
                      {msg.action.action_type === 'DRAFT_LEAVE' && <>
                        {msg.action.data?.leave_date && <div>Ngày nghỉ: <b>{readableISODate(String(msg.action.data.leave_date))}</b></div>}
                        {msg.action.data?.session && <div>Buổi nghỉ: {({ MORNING: 'Buổi sáng', AFTERNOON: 'Buổi chiều', FULL_DAY: 'Cả ngày' } as Record<string, string>)[String(msg.action.data.session)]}</div>}
                      </>}
                      {msg.action.action_type === 'DRAFT_FIX' && <>
                        {msg.action.data?.work_date && <div>Ngày công: <b>{readableISODate(String(msg.action.data.work_date))}</b></div>}
                        {msg.action.data?.event_type && <div>Lượt công: {msg.action.data.event_type === 'CHECK_IN' ? 'Vào làm' : 'Ra về'}</div>}
                        {msg.action.data?.requested_time && <div>Giờ đề nghị: {msg.action.data.requested_time}</div>}
                      </>}
                      {msg.action.data?.reason && <div>Lý do: {msg.action.data.reason}</div>}
                      <Button type="primary" block onClick={() => handleActionClick(msg.action!)}
                        style={{ marginTop: 12, height: 'auto', minHeight: 32, whiteSpace: 'normal' }}>
                        Kiểm tra và hoàn thiện đơn
                      </Button>
                    </Card> : msg.action.action_type !== 'SHOW_DATA' && <Button
                      type="default" icon={<ThunderboltOutlined />} onClick={() => handleActionClick(msg.action!)}
                      style={{ borderColor: '#bfdbfe', color: '#1d4ed8', height: 'auto', minHeight: 36, whiteSpace: 'normal', textAlign: 'left', maxWidth: '100%' }}
                    >
                      {({ OPEN_KNOWLEDGE: 'Mở quản lý tài liệu', OPEN_SOURCE: 'Xem nguồn trích dẫn',
                        OPEN_REPORT: 'Xem báo cáo', OPEN_APPROVALS: 'Mở danh sách chờ duyệt',
                        NAVIGATE: 'Mở màn hình liên quan' } as Record<string, string>)[msg.action.action_type]}
                    </Button>}
                  </div>
                )}
              </div>
              <div
                style={{
                  fontSize: 11,
                  color: '#94a3b8',
                  marginTop: 4,
                  textAlign: msg.role === 'user' ? 'right' : 'left',
                }}
              >
                {msg.timestamp}
              </div>
            </div>
          </div>
        ))}

        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: '50%',
                backgroundColor: '#f0f5ff',
                color: '#1677ff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <RobotOutlined />
            </div>
            <div
              style={{
                backgroundColor: '#ffffff',
                padding: '10px 16px',
                borderRadius: '4px 16px 16px 16px',
                border: '1px solid #e2e8f0',
              }}
            >
              <Spin size="small" /> <span style={{ marginLeft: 8, fontSize: 13, color: '#64748b' }}>Đang suy nghĩ...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Suggestions stay collapsed so the conversation remains the focus. */}
      {!activeDraftId && suggestionsLoading && <Typography.Text type="secondary">Đang tải gợi ý...</Typography.Text>}
      {!activeDraftId && suggestionsError && <Space style={{ margin: '10px 0 8px' }}>
        <Typography.Text type="secondary">Chưa tải được gợi ý.</Typography.Text>
        <Button size="small" onClick={() => setSuggestionsRetry((value) => value + 1)}>Thử lại</Button>
      </Space>}
      {!activeDraftId && !suggestionsError && suggestions.length > 0 && <div style={{ margin: '10px 0 8px', flexShrink: 0 }}>
        <Dropdown trigger={['click']} placement="topLeft"
          menu={{ items: suggestions.map((suggestion) => ({
            key: suggestion.suggestion_id, label: suggestion.prompt,
          })), onClick: ({ key }) => {
            const suggestion = suggestions.find((item) => item.suggestion_id === key);
            if (suggestion) void handleSendMessage(suggestion.prompt, true, suggestion.catalog_version ? suggestion.suggestion_id : undefined);
          }, style: { maxHeight: 'min(320px, 60dvh)', overflowY: 'auto', maxWidth: 'calc(100vw - 48px)' } }}>
          <Button type="text" size="small" icon={<ThunderboltOutlined />} disabled={loading} style={{ color: '#64748b' }}>Gợi ý câu hỏi</Button>
        </Dropdown>
      </div>}
      {retryMessage && <Button size="small" onClick={() => void handleSendMessage(retryMessage.text, retryMessage.startNew, retryMessage.suggestionId, retryMessage.inputs)}>Thử lại yêu cầu</Button>}

      {activeDraftId && <Space wrap style={{ marginBottom: 8, flexShrink: 0 }}>
        {inputOptions.filter((option) => option.field === missingFields[0]).map((option) => <Button key={`${option.field}-${option.value}`} size="small" disabled={loading} onClick={() => handleSendMessage(option.label, false, undefined, { [option.field]: option.value })}>{option.label}</Button>)}
        {missingFields[0] === 'start_date' && ['Tháng này', 'Tháng trước'].map((label) => <Button key={label} size="small" disabled={loading} onClick={() => handleSendMessage(label)}>{label}</Button>)}
        {missingFields[0] === 'session' && [
          ['MORNING', 'Buổi sáng'], ['AFTERNOON', 'Buổi chiều'], ['FULL_DAY', 'Cả ngày'],
        ].map(([value, label]) => <Button key={value} size="small" disabled={loading} onClick={() => handleSendMessage(label, false, undefined, { session: value })}>{label}</Button>)}
        {missingFields[0] === 'leave_type_id' && !inputOptions.some((option) => option.field === 'leave_type_id') && ['Phép năm', 'Nghỉ ốm', 'Nghỉ không lương'].map((label) => <Button key={label} size="small" disabled={loading} onClick={() => handleSendMessage(label)}>{label}</Button>)}
        {missingFields[0] === 'event_type' && ['CHECK_IN', 'CHECK_OUT'].map((value) => <Button key={value} size="small" disabled={loading} onClick={() => handleSendMessage(value === 'CHECK_IN' ? 'check-in' : 'check-out', false, undefined, { event_type: value })}>{value === 'CHECK_IN' ? 'Check-in' : 'Check-out'}</Button>)}
        <Button size="small" disabled={loading} onClick={() => handleSendMessage('hủy bản nháp')}>Hủy bản nháp</Button>
      </Space>}

      {editingRunId && <Space style={{ marginBottom: 8 }}><Typography.Text type="secondary">Đang chỉnh báo cáo vừa xem</Typography.Text><Button size="small" type="text" onClick={() => setEditingRunId(null)}>Kết thúc</Button></Space>}
      <ChatReportPreview key={previewRunId || 'closed'} runId={previewRunId} onClose={() => setPreviewRunId(null)} onRevise={(id) => { setEditingRunId(id); setInputVal(''); }} />
      {/* Input row */}
      <div style={{ display: 'flex', gap: 8, marginTop: 'auto', flexShrink: 0 }}>
        <Input.TextArea
          rows={2}
          value={inputVal}
          onChange={(e) => setInputVal(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSendMessage();
            }
          }}
          placeholder="Nhập câu hỏi hoặc yêu cầu (ví dụ: 'Tôi còn bao nhiêu ngày phép?')..."
          style={{ resize: 'none', borderRadius: 8 }}
        />
        <Button
          type="primary"
          icon={<SendOutlined />}
          style={{ height: 'auto', borderRadius: 8, padding: '0 20px' }}
          onClick={() => handleSendMessage()}
          loading={loading}
        >
          Gửi
        </Button>
      </div>
      <Modal open={!!reference} onCancel={() => setReference(null)} footer={null} title={reference?.title} width={720}>
        <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{reference?.content}</Typography.Paragraph>
        {reference?.source_url && /^https?:\/\//i.test(reference.source_url) && <a href={reference.source_url} target="_blank" rel="noopener noreferrer">Mở tài liệu gốc</a>}
      </Modal>
    </Modal>
  );
};
