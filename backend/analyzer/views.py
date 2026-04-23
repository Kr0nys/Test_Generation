from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from .models import AnalysisSession, TestGenerationTask, UploadedFile
from .serializers import (
    AnalysisSessionSerializer, AnalysisSessionCreateSerializer,
    TestGenerationTaskSerializer, TestGenerationConfigSerializer
)
from .tasks import analyze_project, generate_tests_task


class AnalysisSessionViewSet(viewsets.ModelViewSet):
    queryset = AnalysisSession.objects.all()
    serializer_class = AnalysisSessionSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        return AnalysisSession.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_files(self, request, pk=None):
        """Загрузка файлов для сессии"""
        session = self.get_object()
        files = request.FILES.getlist('files')  # ✅ Получаем файлы из request.FILES

        if not files:
            return Response(
                {'error': 'No files provided'},
                status=status.HTTP_400_BAD_REQUEST
            )

        uploaded = []
        for file in files:
            uploaded_file = UploadedFile.objects.create(
                session=session,
                file=file,
                original_name=file.name,
                file_size=file.size
            )
            uploaded.append({
                'id': uploaded_file.id,
                'name': uploaded_file.original_name,
                'size': uploaded_file.file_size
            })
            # Сохраняем путь для анализа
            session.uploaded_files.append(uploaded_file.file.path)

        session.save(update_fields=['uploaded_files', 'updated_at'])

        # Запускаем задачу анализа
        analyze_project.delay(str(session.id))

        return Response({
            'status': 'uploaded',
            'files': uploaded,
            'task_id': str(session.id)
        }, status=status.HTTP_200_OK)

    @action(
        detail=True,
        methods=['post'],
        url_path='generate_tests',
        parser_classes=[JSONParser]
    )
    def generate_tests(self, request, pk=None):
        session = self.get_object()

        if session.status != 'ANALYZED':
            return Response(
                {'error': 'Session must be analyzed first'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = TestGenerationConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        task = TestGenerationTask.objects.create(
            session=session,
            config=serializer.validated_data
        )

        generate_tests_task.delay(str(task.id))

        return Response({
            'task_id': str(task.id),
            'status': 'pending'
        }, status=status.HTTP_202_ACCEPTED)


class TestGenerationTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = TestGenerationTask.objects.all()
    serializer_class = TestGenerationTaskSerializer

    def get_queryset(self):
        return TestGenerationTask.objects.filter(session__user=self.request.user)

    @action(detail=True, methods=['get'])
    def download(self, request, pk=None):
        from django.http import HttpResponse
        task = self.get_object()
        response = HttpResponse(task.generated_tests, content_type='text/plain')
        response['Content-Disposition'] = f'attachment; filename="tests_{task.id}.py"'
        return response