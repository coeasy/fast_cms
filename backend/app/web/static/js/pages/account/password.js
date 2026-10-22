import {
  $, api, appUrl, esc, setAccessToken, setFieldError, setLoading, state, toast,
} from '../../core.js';

let policy = { minLength: 8, description: '' };

export async function init() {
  policy = readPolicy();
  $('#new-password-help').textContent = policy.description;
  $('#password-policy').textContent = policy.description;
  $('#policy-min').textContent = String(policy.minLength);

  const forced = Boolean(state.user?.must_change_password);
  if (forced) {
    $('#password-title').textContent = '首次登录需修改密码';
    $('#forced-alert').classList.remove('hidden');
  }

  $('#password-form').addEventListener('submit', submit);
}

function readPolicy() {
  const raw = Number(state.publicConfig?.['auth.password_min_length']);
  const minLength = Number.isFinite(raw) && raw >= 8 ? Math.min(raw, 64) : 8;
  const description = String(
    state.publicConfig?.['auth.password_rule'] || `至少 ${minLength} 位，必须同时包含字母和数字，且不能包含用户名`,
  );
  return { minLength, description };
}

function checkNewPassword(field) {
  const value = field.value;
  let message = '';
  if (!value) message = '请输入新密码';
  else if (value.length < policy.minLength) message = `密码至少 ${policy.minLength} 位`;
  else if (!/[A-Za-z]/.test(value) || !/\d/.test(value)) message = '密码必须同时包含字母和数字';
  else if (state.user?.username && value.toLowerCase().includes(String(state.user.username).toLowerCase())) {
    message = '密码不能包含用户名';
  }
  setFieldError(field, message);
  return !message;
}

async function submit(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const oldField = $('#pw-old');
  const newField = $('#pw-new');
  const confirmField = $('#pw-confirm');

  let valid = true;
  if (!oldField.value) {
    setFieldError(oldField, '请输入当前密码');
    valid = false;
  } else {
    setFieldError(oldField, '');
  }
  if (!checkNewPassword(newField)) valid = false;
  if (!confirmField.value) {
    setFieldError(confirmField, '请再次输入新密码');
    valid = false;
  } else if (confirmField.value !== newField.value) {
    setFieldError(confirmField, '两次输入的密码不一致');
    valid = false;
  } else {
    setFieldError(confirmField, '');
  }
  if (!valid) return;

  const button = $('#password-submit');
  setLoading(button, true);
  try {
    const result = await api.post('/auth/change-password', {
      old_password: oldField.value,
      new_password: newField.value,
    });
    setAccessToken(result.access_token);
    state.user = result.user;
    if (state.me) state.me.user = result.user;
    toast('密码修改成功，其他会话已失效', 'success');
    setTimeout(() => {
      window.location.replace(appUrl(state.isAdmin ? 'dashboard' : 'account/profile'));
    }, 600);
  } catch (error) {
    if (error?.code === 'AUTH_INVALID_CREDENTIALS' || error?.status === 400) {
      setFieldError(oldField, error.message || '当前密码不正确');
    }
  } finally {
    setLoading(button, false);
  }
}
