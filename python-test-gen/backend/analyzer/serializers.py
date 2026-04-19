from rest_framework import serializers
from .models import AnalysisSession, TestGenerationTask, UploadedFile


class UploadedFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UploadedFile
        fields = ['id', 'original_name', 'file_size', 'uploaded_at', 'file']
        read_only_fields = ['file_size', 'uploaded_at']


class AnalysisSessionSerializer(serializers.ModelSerializer):
    files = UploadedFileSerializer(many=True, read_only=True)
    file_count = serializers.SerializerMethodField()

    class Meta:
        model = AnalysisSession
        fields = ['id', 'name', 'user', 'python_version', 'dependencies', 'status',
                  'created_at', 'updated_at', 'expires_at', 'metrics', 'report_text',
                  'error_message', 'uploaded_files', 'files', 'file_count']
        read_only_fields = ['user', 'status', 'created_at', 'updated_at', 'expires_at',
                            'metrics', 'report_text', 'error_message']

    def get_file_count(self, obj):
        return obj.files.count()


class AnalysisSessionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnalysisSession
        fields = ['name', 'python_version', 'dependencies']


class TestGenerationTaskSerializer(serializers.ModelSerializer):
    session_id = serializers.UUIDField(source='session.id', read_only=True)

    class Meta:
        model = TestGenerationTask
        fields = ['id', 'session_id', 'config', 'status', 'generated_tests',
                  'created_at', 'updated_at', 'error_message']
        read_only_fields = ['status', 'generated_tests', 'created_at', 'updated_at', 'error_message']


class TestGenerationConfigSerializer(serializers.Serializer):
    detail_level = serializers.ChoiceField(
        choices=[('basic', 'Basic'), ('advanced', 'Advanced'), ('full', 'Full')],
        default='basic'
    )
    use_mocks = serializers.BooleanField(default=True)
    async_support = serializers.BooleanField(default=False)
    timeout_seconds = serializers.IntegerField(default=300, min_value=60, max_value=1800)
    include_edge_cases = serializers.BooleanField(default=False)