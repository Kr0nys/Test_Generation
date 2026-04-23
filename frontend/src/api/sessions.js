import api from './axios';

export const sessionsAPI = {
  getAll: async () => {
    const response = await api.get('/sessions/');
    return response.data;
  },

  getById: async (id) => {
    const response = await api.get(`/sessions/${id}/`);
    return response.data;
  },

  create: async (data) => {
    const response = await api.post('/sessions/', data);
    return response.data;
  },

  uploadFiles: async (sessionId, files) => {
    const formData = new FormData();
    files.forEach(file => formData.append('files', file));

    const response = await api.post(`/sessions/${sessionId}/upload_files/`, formData, {
      headers: {
      'Content-Type': undefined,
    }
    });
    return response.data;
  },

  getStatus: async (sessionId) => {
    const response = await api.get(`/sessions/${sessionId}/status/`);
    return response.data;
  },

  generateTests: async (sessionId, config) => {
    const response = await api.post(`/sessions/${sessionId}/generate_tests/`, config);
    return response.data;
  },

  delete: async (id) => {
    await api.delete(`/sessions/${id}/`);
  }
};