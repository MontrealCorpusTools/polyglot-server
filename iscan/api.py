import os
import time
import json
import zipfile
import base64
from distutils.util import strtobool
from uuid import uuid1

import django
from django.core.exceptions import ValidationError
from django.conf import settings
from django.http.response import FileResponse, HttpResponse
from django.db.models import Q
from django.contrib.auth.models import User
from django.contrib.auth import password_validation
from rest_framework import generics, permissions, viewsets, status, pagination
from rest_framework.response import Response
from rest_framework.decorators import action

from neo4j import exceptions as neo4j_exceptions


from polyglotdb import CorpusContext
from polyglotdb.query.base.func import Count

from . import models
from . import serializers
from .utils import get_used_ports
from .tasks import import_corpus_task, run_query_task, run_enrichment_task, reset_enrichment_task, delete_enrichment_task, run_query_export_task, run_query_generate_subset_task, run_spade_script_task

import logging
log = logging.getLogger('polyglot_server')


class UserViewSet(viewsets.ModelViewSet):
    model = User
    queryset = User.objects.all()
    serializer_class = serializers.UserSerializer

    def create(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            user = User.objects.get(username=request.data['username'])
            return Response('Username is taken.', status=status.HTTP_409_CONFLICT)
        except User.DoesNotExist:
            pass
        try:
            password_validation.validate_password(request.data['password'])
        except ValidationError as e:
            return Response(" ".join(e.messages), status=status.HTTP_400_BAD_REQUEST)
        user = User.objects.create_user(username=request.data['username'], password=request.data['password'])
        user.profile.user_type = request.data['user_type']
        user.save()
        user.profile.update_role_permissions()
        serialized = serializers.UserSerializer(user)
        return Response(serialized.data, status=status.HTTP_201_CREATED)

    def list(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        users = User.objects.select_related('profile').all()
        return Response(self.serializer_class(users, many=True).data)

    def destroy(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        user = self.get_object()
        tutorial_corpus = user.profile.get_tutorial_corpus()
        if tutorial_corpus is not None:
            tutorial_corpus.database.delete()
        return super(UserViewSet, self).destroy(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return super(UserViewSet, self).retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        user = self.get_object()
        user.username = request.data['username']
        ignore_perms = request.data['user_type'] != user.profile.user_type
        if ignore_perms:
            user.profile.user_type = request.data['user_type']
        user.save()
        for corpus_id, perm_data in request.data['corpus_permissions'].items():
            perm = models.CorpusPermissions.objects.get(user=user, corpus_id=int(corpus_id))
            if ignore_perms:
                perm.set_role_permissions()
            else:
                for k, v in perm_data.items():
                    if k == 'corpus':
                        continue
                    setattr(perm, k, v)
            perm.save()
        user = self.get_object()
        serialized = serializers.UserSerializer(user)
        return Response(serialized.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'])
    def current_user(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return Response(self.serializer_class(request.user).data)

    @action(detail=False, methods=['put'])
    def change_password(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            password_validation.validate_password(request.data['password'])
        except ValidationError as e:
            return Response(" ".join(e.messages), status=status.HTTP_400_BAD_REQUEST)
        request.user.set_password(request.data['password'])
        request.user.save()
        return Response(self.serializer_class(request.user).data)

    @action(detail=True, methods=['post'])
    def create_tutorial_corpus(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        user = self.get_object()
        c = user.profile.get_tutorial_corpus()
        if c:
            return Response(serializers.CorpusSerializer(c).data,
                            status=status.HTTP_304_NOT_MODIFIED)
        c = user.profile.create_tutorial_corpus()
        return Response(serializers.CorpusSerializer(c).data,
                            status=status.HTTP_201_CREATED)


class AppViewSet(viewsets.ViewSet):
    def list(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        apps = ['base']
        for a in settings.INSTALLED_APPS:
            if a.startswith('iscan.'):
                apps.append(a.replace('iscan.', ''))
        return Response(apps)


class RoleChoiceViewSet(viewsets.ViewSet):
    def list(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        choices = [{'id': k, 'name':v} for k, v in models.Profile.TYPE_CHOICES]
        return Response(choices)


class CorpusTypeChoiceViewSet(viewsets.ViewSet):
    def list(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        choices = [{'id': k, 'name':v} for k, v in models.Corpus.TYPE_CHOICES]
        return Response(choices)


class DatabaseViewSet(viewsets.ModelViewSet):
    model = models.Database
    queryset = models.Database.objects.all()
    serializer_class = serializers.DatabaseSerializer

    def create(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        used_ports = get_used_ports()
        current_ports = []
        data_dict = {'name': request.data.get('name'),
                     'neo4j_http_port': request.data.get('neo4j_http_port', None),
                     'neo4j_https_port': request.data.get('neo4j_https_port', None),
                     'neo4j_bolt_port': request.data.get('neo4j_bolt_port', None),
                     'neo4j_admin_port': request.data.get('neo4j_admin_port', None),
                     'influxdb_http_port': request.data.get('influxdb_http_port', None),
                     'influxdb_meta_port': request.data.get('influxdb_meta_port', None),
                     'influxdb_udp_port': request.data.get('influxdb_udp_port', None),
                     'influxdb_admin_port': request.data.get('influxdb_admin_port', None)}
        ports = {'neo4j': settings.BASE_NEO4J_PORT, 'influxdb': settings.BASE_INFLUXDB_PORT}
        for k, v in data_dict.items():
            if 'port' not in k:
                continue
            if v is None:
                if 'neo4j' in k:
                    port_key = 'neo4j'
                else:
                    port_key = 'influxdb'
                while True:
                    if ports[port_key] not in used_ports and ports[port_key] not in current_ports:
                        data_dict[k] = ports[port_key]
                        current_ports.append(ports[port_key])
                        ports[port_key] += 1
                        break
                    ports[port_key] += 1
        serializer = serializers.DatabaseSerializer(data=data_dict)
        if serializer.is_valid():
            database = serializer.save()
            database.install()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        else:
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def list(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if request.user.is_superuser:
            databases = models.Database.objects.all()
        else:
            databases = models.Database.objects.filter(corpora__user_permissions__user=request.user,
                                                       corpora__user_permissions__can_access_database=True).all()

        return Response(self.serializer_class(databases, many=True).data)

    @action(detail=False, methods=['post'])
    def refresh_databases(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if request.user.is_superuser:
            corpora = models.Corpus.objects.all()
            #FIXME TOO MUCH HARDCODING
            corpus_names = [x.name for x in corpora]
            requery = False
            for dataset in os.listdir(settings.SOURCE_DATA_DIRECTORY):
                if dataset not in corpus_names:
                    d, _ = models.Database.objects.get_or_create(name=dataset)
                    c = models.Corpus.objects.create(name=dataset, database=d)
                    if 'input_format' in c.configuration_data:
                        input_format = c.configuration_data['input_format'][0].upper()
                        if input_format == "MFA":
                            input_format = Corpus.MFA
                        if input_format == "MAUS":
                            input_format = Corpus.MAUS
                        if input_format == "FAVE":
                            input_format = Corpus.FAVE
                        if input_format == "LABCAT":
                            input_format = Corpus.LABCAT
                        if input_format == "PARTITUR":
                            input_format = Corpus.PARTITUR
                        if input_format == "TIMIT":
                            input_format = Corpus.TIMIT
                        if input_format == "BUCKEYE":
                            input_format = Corpus.BUCKEYE
                        c.input_format = input_format
                        c.save()
            databases = models.Database.objects.all()
            return Response(self.serializer_class(databases, many=True).data)
        return Response(status=status.HTTP_401_UNAUTHORIZED)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        database = self.get_object()
        if not request.user.is_superuser:
            permissions = models.CorpusPermissions.objects.filter(user=request.user, corpus__database=database,
                                                                  can_access_database=True).all()
            if not len(permissions):
                return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            success = database.start()
        except Exception as e:
            return Response(data=str(e), status=status.HTTP_423_LOCKED)
        return Response(data=success)

    @action(detail=True, methods=['post'])
    def stop(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        database = self.get_object()
        if not request.user.is_superuser:
            permissions = models.CorpusPermissions.objects.filter(user=request.user, corpus__database=database,
                                                                  can_access_database=True).all()
            if not len(permissions):
                return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            success = database.stop()
        except Exception as e:
            return Response(data=str(e), status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(data=success)

    def destroy(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return super(DatabaseViewSet, self).destroy(request, pk)

    @action(detail=True, methods=['get'])
    def ports(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        database = self.get_object()
        if not request.user.is_superuser:
            permissions = models.CorpusPermissions.objects.filter(user=request.user, corpus__database=database).all()
            permissions = [x.can_access_database for x in permissions]
            if not len(permissions) or not any(permissions):
                return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = database.ports
        return Response(data)

    @action(detail=True, methods=['get'])
    def data_directory(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        database = self.get_object()
        if not request.user.is_superuser:
            permissions = models.CorpusPermissions.objects.filter(user=request.user, corpus__database=database).all()
            permissions = [x.can_access_database for x in permissions]
            if not len(permissions) or not any(permissions):
                return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = database.directory
        return Response(data)

    @action(detail=True, methods=['get'])
    def corpora(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        database = self.get_object()
        if not request.user.is_superuser:
            permissions = models.CorpusPermissions.objects.filter(user=request.user, corpus__database=database).all()
            permissions = [x.can_access_database for x in permissions]
            if not len(permissions) or not any(permissions):
                return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpora = models.Corpus.objects.filter(database=database)
        serializer = serializers.CorpusSerializer(corpora, many=True)
        return Response(serializer.data)


class CorpusViewSet(viewsets.ModelViewSet):
    model = models.Corpus
    queryset = models.Corpus.objects.all()
    serializer_class = serializers.CorpusSerializer

    def list(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpora = models.Corpus.objects.filter(user_permissions__user=request.user, user_permissions__can_query=True).all()

        return Response(self.serializer_class(corpora, many=True).data)

    def create(self, request, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = {k: v for k, v in request.data.items()}
        data['database'] = models.Database.objects.get(pk=int(data['database']))
        data['source_directory'] = os.path.join(settings.SOURCE_DATA_DIRECTORY, data['source_directory'])
        instance = models.Corpus.objects.create(name=data['name'], database=data['database'])
        return Response(self.serializer_class(instance).data)

    @action(detail=True, methods=['post'])
    def import_corpus(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot import",
                    status=status.HTTP_400_BAD_REQUEST)

        response = Response('Import started', status=status.HTTP_202_ACCEPTED)
        task_id = import_corpus_task.delay(corpus.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response

    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("database not running")
        running_enrichments = models.Enrichment.objects.filter(corpus=corpus, running=True).all()
        if len(running_enrichments):
            return Response('enrichment running')
        running_queries = models.Query.objects.filter(corpus=corpus, running=True).all()
        if len(running_queries):
            return Response('query running')
        return Response('ready')

    @action(detail=True, methods=['get'])
    def property_values(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        type = request.GET.get('type', None)

        prop = request.GET.get('prop', 'label')
        corpus = self.get_object()
        with CorpusContext(corpus.config) as c:
            ann = getattr(c, type)
            resp = sorted(c.query_metadata(ann).levels(getattr(ann, prop)))
        return Response(resp)

    @action(detail=True, methods=['get'])
    def autocomplete(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        prefix = request.GET.get('prefix', None)
        category = request.GET.get('category', None)
        if category in ['speaker', 'discourse']:
            category = category.title()
        else:
            category += '_type'
        prop = request.GET.get('prop', 'label')
        if prefix is None:
            return Response("Please provide a prefix",
                    status=status.HTTP_400_BAD_REQUEST)

        for x in ['\'', '\"']:
            #Escape characters
            prefix = prefix.replace(x, '\\{}'.format(x))

        corpus = self.get_object()
        with CorpusContext(corpus.config) as c:
            statement = """MATCH (n:{category}:{corpus_name})
                         WHERE n.{prop} =~ '(?i){prefix}.*'
                         RETURN DISTINCT n.{prop}
                         LIMIT 10""".format(corpus_name=c.cypher_safe_name, category=category, prop=prop, prefix=prefix)
            resp = c.execute_cypher(statement).value()
        return Response(resp)

    @action(detail=True, methods=['get'])
    def speakers(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            speakers = c.speakers

        return Response(speakers)

    @action(detail=True, methods=['get'])
    def words(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        count = request.GET.get('count', None)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if count is None or not count.isdigit():
            return Response(
                'There must be a requested number of words',
                status=status.HTTP_400_BAD_REQUEST)
        with CorpusContext(corpus.config) as c:
            statement = '''MATCH (n:{}:word)
            WITH n.label as label, count(n.label) as c
            ORDER BY c DESC, label
            with label
            LIMIT {}
            return label'''.format(c.cypher_safe_name, count)
            results = c.execute_cypher(statement)
            results = [x['label'] for x in results]
        return Response(results)

    @action(detail=True,methods=['get'])
    def default_subsets(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        subset_class = request.GET.get('subset_class', 'syllabics')
        corpus = models.Corpus.objects.get(pk=pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        if subset_class not in ['syllabics', 'sibilants', 'stressed_vowels']:
            return Response(
                'Invalid subset class',
                status=status.HTTP_400_BAD_REQUEST)

        if subset_class == 'syllabics':
            subset = corpus.syllabics
        elif subset_class == "sibilants":
            subset = corpus.sibilants
        elif subset_class == "stressed_vowels":
            subset = corpus.stressed_vowels
        return Response(json.dumps(subset))

    @action(detail=True, methods=['get'])
    def phones(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            phones = c.query_lexicon(c.lexicon_phone).columns(c.lexicon_phone.label).all()

        return Response(phones.to_json())

    @action(detail=True, methods=['get'])
    def phone_set(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            q = c.query_lexicon(c.lexicon_phone).columns(c.lexicon_phone.label.column_name('label'))
            phones = q.all()

            # Remove duplicates to get phone set
            phones = sorted(x['label'] for x in phones)

        return Response(phones)

    @action(detail=True, methods=['get'])
    def word_set(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            q = c.query_lexicon(c.lexicon_word).columns(c.lexicon_word.label.column_name('label'))
            words = q.all()

            # Remove duplicates to get phone set
            words = sorted(set(x['label'] for x in words))
        return Response(words)

    @action(detail=True, methods=['get'])
    def hierarchy(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        if corpus.database.status != 'R':
            return Response('Database is not running', status=status.HTTP_400_BAD_REQUEST)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        try:
            with CorpusContext(corpus.config) as c:
                hierarchy = c.hierarchy
                data = serializers.HierarchySerializer(hierarchy).data
        except neo4j_exceptions.ServiceUnavailable:
            corpus.database.status = 'S'
            corpus.database.neo4j_pid = None
            corpus.database.influxdb_pid = None
            corpus.database.save()
            return Response('Database is not running', status=status.HTTP_400_BAD_REQUEST)
        return Response(data)

    @action(detail=True, methods=['get'])
    def utterance_pitch_track(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_view_detail=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        utterance_id = request.query_params.get('utterance_id', None)
        if utterance_id is None:
            return Response(None)
        source = request.query_params.get('source', 'praat')
        min_pitch = int(request.query_params.get('min_pitch', 50))
        max_pitch = int(request.query_params.get('max_pitch', 500))
        with CorpusContext(corpus.config) as c:
            results = c.analyze_utterance_pitch(utterance_id, source=source, min_pitch=min_pitch, max_pitch=max_pitch)
        pitch_data = {}
        pitch_data['pitch_track'] = serializers.PitchPointSerializer([x for x in results if x.F0 != None],
                                                                     many=True).data
        return Response(pitch_data['pitch_track'])

    @action(detail=True, methods=['post'])
    def save_utterance_pitch_track(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = self.get_object()
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_edit=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        id = request.data['id']
        track = request.data['track']
        with CorpusContext(corpus.config) as c:
            time_stamp = c.update_utterance_pitch_track(id, track)
        return Response({'success': True, 'time_stamp': time_stamp})


class SourceChoiceViewSet(viewsets.ViewSet):
    def list(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        choices = os.listdir(settings.SOURCE_DATA_DIRECTORY)
        return Response(choices)


class DiscourseViewSet(viewsets.ViewSet):
    def list(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        with CorpusContext(corpus.config) as c:
            discourses = c.discourses

        return Response(discourses)

    @action(detail=False, methods=['get'])
    def properties(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            props = c.query_metadata(c.discourse).grouping_factors()
            data = []
            data.append({'name':'name', 'options': c.discourses})
            for p in props:
                data.append({'name': p, 'options': c.query_metadata(c.discourse).levels(getattr(c.discourse, p))})
        return Response(data)


class SpeakerViewSet(viewsets.ViewSet):
    def list(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        with CorpusContext(corpus.config) as c:
            speakers = c.speakers

        return Response(speakers)

    @action(detail=False, methods=['get'])
    def properties(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        with CorpusContext(corpus.config) as c:
            props = c.query_metadata(c.speaker).grouping_factors()
            data = []
            data.append({'name':'name', 'options': c.speakers})
            for p in props:
                data.append({'name': p, 'options': c.query_metadata(c.speaker).levels(getattr(c.speaker, p))})
        return Response(data)


class SubannotationViewSet(viewsets.ViewSet):
    def create(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_annotate=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        a_type = request.data.pop('annotation_type')
        a_id = request.data.pop('annotation_id')
        s_type = request.data.pop('subannotation_type')
        data = request.data['subannotation']
        with CorpusContext(corpus.config) as c:
            att = getattr(c, a_type)
            q = c.query_graph(att).filter(getattr(att, 'id') == a_id)
            res = q.all()[0]
            res.add_subannotation(s_type, **data)
            data = serializers.serializer_factory(c.hierarchy, a_type, top_level=True, with_subannotations=True)(
                res).data
            data = data[a_type][s_type][-1]
        return Response(data)

    def update(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_edit=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = request.data
        s_id = data.pop('id')

        props = []
        prop_template = 's.%s = {%s}'
        with CorpusContext(corpus.config) as c:
            statement = '''MATCH (s:{corpus_name}) WHERE s.id = {{s_id}} RETURN s'''.format(corpus_name=c.cypher_safe_name)
            res = c.execute_cypher(statement, s_id=s_id)
            for r in res:
                for x in r['s'].labels:
                    if x in c.hierarchy.subannotation_properties:
                        s_type = x
            for k, v in data.items():
                props.append(prop_template % (k, k))
                if c.hierarchy.has_subannotation_property(s_type, k):
                    for name, t in c.hierarchy.subannotation_properties[s_type]:
                        if name == k:
                            data[k] = t(v)
            set_props = ',\n'.join(props)
            statement = '''MATCH (s:{corpus_name}) WHERE s.id = {{s_id}}
            SET {set_props}'''.format(corpus_name=c.cypher_safe_name, set_props=set_props)
            c.execute_cypher(statement, s_id=s_id, **data)
        return Response(None)

    def destroy(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_edit=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        with CorpusContext(corpus.config) as c:
            statement = '''MATCH (s:{corpus_name}) WHERE s.id = {{s_id}}
            DETACH DELETE s'''.format(corpus_name=c.cypher_safe_name)
            c.execute_cypher(statement, s_id=pk)
        return Response(None)


class AnnotationViewSet(viewsets.ViewSet):
    @action(detail=True, methods=['get'])
    def sound_file(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_listen=True).all()
        if not len(permissions):
           return Response(status=status.HTTP_401_UNAUTHORIZED)
        with CorpusContext(corpus.config) as c:
            fname = c.utterance_sound_file(pk, 'consonant')

        response = FileResponse(open(fname, "rb"), content_type='audio/wav')
        return response


class EnrichmentViewSet(viewsets.ModelViewSet):
    model = models.Enrichment
    serializer_class = serializers.EnrichmentSerializer

    def get_queryset(self):
        return models.Enrichment.objects.filter(corpus__pk=self.kwargs['corpus_pk'])

    def list(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        enrichments = models.Enrichment.objects.filter(corpus=corpus).all()
        return Response(serializers.EnrichmentSerializer(enrichments, many=True).data)

    def create(self, request, corpus_pk=None, *args, **kwargs):
        log.info("Creating an enrichment.")
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        data = request.data
        enrich_type = data['enrichment_type']
        if enrich_type in ['pitch', 'formants', 'intensity']:
            if not data.get('source', ''):
                return Response(
                    'A program to use for this enrichment must be specified.',
                    status=status.HTTP_400_BAD_REQUEST)
            name = 'Encode {} tracks'.format(enrich_type)
        elif enrich_type == 'praat_script':
            name = 'Enrich praat_script'
        elif enrich_type  in ['pauses', 'utterances', 'syllables']:
            name = 'Encode {}'.format(enrich_type)
        # Subset validation
        elif enrich_type == 'subset':
            label = data.get('subset_label', '')
            if not label:
                return Response(
                    'The subset must have a name.',
                    status=status.HTTP_400_BAD_REQUEST)
            if not data.get('annotation_labels', []):
                return Response(
                    'The subset cannot be empty.',
                    status=status.HTTP_400_BAD_REQUEST)
            with CorpusContext(corpus.config) as c:
                if c.hierarchy.has_token_subset(data.get('annotation_type', ''), data.get('subset_label', '')) or \
                        c.hierarchy.has_type_subset(data.get('annotation_type', ''), data.get('subset_label', '')):
                    return Response(
                            "The {} subset already exists".format(data.get('subset_label', '')),
                            status=status.HTTP_400_BAD_REQUEST)
            name = 'Encode {} subset'.format(label)

        #Stress pattern validation
        elif request.data['enrichment_type'] in ['patterned_stress']:
            prop = data.get('word_property', '')
            if not prop:
                return Response(
                    'There must be a word property.',
                    status=status.HTTP_400_BAD_REQUEST)
            name = 'Encode lexical stress from {}'.format(prop)

        # Hierarchical property validation
        elif request.data['enrichment_type'] in ['hierarchical_property']:
            label = data.get('property_label', '')
            if not label:
                return Response(
                    'The hierarchical property must have a name.',
                    status=status.HTTP_400_BAD_REQUEST)
            higher = data.get('higher_annotation', '')
            if not higher:
                return Response(
                    'Higher annotation must be specified.',
                    status=status.HTTP_400_BAD_REQUEST)
            lower = data.get('lower_annotation', '')
            if not lower:
                return Response(
                    'Lower annotation must be specified.',
                    status=status.HTTP_400_BAD_REQUEST)
            with CorpusContext(corpus.config) as c:
                annotation_types = c.hierarchy.highest_to_lowest
            if higher not in annotation_types:
                return Response(
                    'Must specify a higher annotation that has been encoded.',
                    status=status.HTTP_400_BAD_REQUEST)
            if lower not in annotation_types:
                return Response(
                    'Must specify a lower annotation that has been encoded.',
                    status=status.HTTP_400_BAD_REQUEST)
            if annotation_types.index(lower) <= annotation_types.index(higher):
                return Response(
                    'The lower annotation level must be lower than the higher annotation level.',
                    status=status.HTTP_400_BAD_REQUEST)
            name = 'Encode {}'.format(label)
        elif enrich_type in ['speaker_csv', 'discourse_csv', 'lexicon_csv', 'phone_csv']:
            name = 'Enrich {}'.format(enrich_type.split('_')[0])
        elif enrich_type in 'importcsv':
            name = 'Temp enrichname {}'.format(uuid1())
        elif enrich_type in ['relativize_pitch', 'relativize_intensity', 'relativize_formants']:
            name = 'Relativize {}'.format(enrich_type.split('_')[1])
        elif enrich_type == 'relativize_property':
            name = 'Relativize {}'.format(data.get('property_name'))
        # Formant validation
        elif enrich_type == 'refined_formant_points':
            dur_thresh = data.get('duration_threshold', '')
            n_iterations = data.get('number_of_iterations', '')
            if dur_thresh:
                try:
                    int(dur_thresh)
                except ValueError:
                    return Response(
                        'The duration threshold must be either blank or an integer.',
                        status=status.HTTP_400_BAD_REQUEST)
            try:
                int(n_iterations)
            except ValueError:
                return Response(
                    'The number of iterations must be an integer.',
                    status=status.HTTP_400_BAD_REQUEST)
            name = 'Encode point formant values via refinement'
        elif enrich_type == 'vot':
            stops_label = data.get('stop_label', '')
            vot_min = data.get('vot_min', '')
            vot_max = data.get('vot_max', '')
            window_min = data.get('window_min', '')
            window_max = data.get('window_max', '')
            if not stops_label:
                return Response(
                        'The stops label must not be blank',
                        status=status.HTTP_400_BAD_REQUEST)
            for x, x_name in [(vot_min, 'VOT minimum'), (vot_max, 'VOT maximum'), (window_min, 'window minimum'), (window_max, 'window maximum')]:
                try: 
                    int(x)
                except ValueError:
                    return Response(
                            'The {} must be an integer'.format(x_name),
                        status=status.HTTP_400_BAD_REQUEST)
            name = 'Calculate VOT of {}'.format(stops_label)
        else:
            return Response(
                'The enrichment type specified is not supported.',
                status=status.HTTP_400_BAD_REQUEST)
        if models.Enrichment.objects.filter(corpus=corpus, name=name).count() > 0:
            return Response(
                'There already exists an enrichment to {} for this corpus.'.format(name),
                status=status.HTTP_409_CONFLICT)
        enrichment = models.Enrichment.objects.create(name=name, corpus=corpus)
        enrichment.config = data
        return Response(serializers.EnrichmentSerializer(enrichment).data)

    @action(detail=True, methods=["post"])
    def create_file(self, request, pk=None, corpus_pk=None, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        enrichment = models.Enrichment.objects.filter(pk=pk, corpus=corpus).get()
        enrich_type = enrichment.config.get('enrichment_type')
        if not request.data.get('text', ''):
            return Response(
                'A file must be included.',
                status=status.HTTP_400_BAD_REQUEST)
        if not request.data.get('file_name', ''):
            return Response(
                'The file must have a name.',
                status=status.HTTP_400_BAD_REQUEST)

        file_path = os.path.join(enrichment.directory, request.data["file_name"])
        with open(file_path, "wb+") as f:
            f.write(base64.b64decode(request.data["text"].split(',')[1]))
        if enrich_type == "vot":
            if not zipfile.is_zipfile(file_path):
                return Response(
                        'The classifier must be in a zip file',
                        status=status.HTTP_400_BAD_REQUEST)
            with zipfile.ZipFile(file_path) as classifier_zip:
                classifier_names = list(filter(lambda x: x.endswith(".pos") or x.endswith(".neg"), classifier_zip.namelist()))
                if len(classifier_names) != 2 or classifier_names[0][:-4:] != classifier_names[1][:-4:]:
                    return Response(
                            'Zip files must contain only the two classifier files which have the same filename except for .neg and .pos file extensions',
                            status=status.HTTP_400_BAD_REQUEST)
                for x in classifier_names:
                    classifier_zip.extract(x, enrichment.directory)
                classifier_path = os.path.join(enrichment.directory, classifier_names[0][:-4:])
                enrichment.config = {**enrichment.config,
                                     **{'classifier': str(classifier_path)}}
                enrichment.save()
            os.remove(file_path)
        elif enrich_type == "importcsv":
            enrichment.name = 'Enrich {} from "{}"'.format(enrichment.config.get("annotation_type"), os.path.basename(request.data['file_name']))
            enrichment.config = {**enrichment.config,
                                 **{'path': str(file_path)}}
            enrichment.save()
        else:
            enrichment.name = 'Enrich {} from {}'.format(enrich_type.split('_')[0], os.path.basename(request.data['file_name']))
            enrichment.config = {**enrichment.config,
                                 **{'path': str(file_path)}}
            enrichment.save()
        return Response(True)

    @action(detail=True, methods=['post'])
    def run(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        enrichment = models.Enrichment.objects.filter(pk=pk, corpus=corpus).get()
        if enrichment.runnable != 'runnable':
            return Response(
                    'The enrichment is not runnable',
                    status=status.HTTP_400_BAD_REQUEST)

        if not enrichment.corpus.database.is_running:
            return Response("Database is not running, cannot run enrichment",
                    status=status.HTTP_400_BAD_REQUEST)
        response = Response(True)
        task_id = run_enrichment_task.delay(enrichment.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response

    @action(detail=True, methods=['post'])
    def reset(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        enrichment = models.Enrichment.objects.filter(pk=pk, corpus=corpus).get()
        if not enrichment.corpus.database.is_running:
            return Response("Database is not running, cannot reset enrichment",
                    status=status.HTTP_400_BAD_REQUEST)
        response = Response(True)
        task_id = reset_enrichment_task.delay(enrichment.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response

    def update(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        enrichment = models.Enrichment.objects.filter(pk=pk, corpus=corpus).get()
        if enrichment is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if not enrichment.corpus.database.is_running:
            return Response("Database is not running, cannot update enrichment",
                    status=status.HTTP_400_BAD_REQUEST)
        enrichment.config = request.data
        enrichment.save()
        return Response(serializers.EnrichmentSerializer(enrichment).data)

    def destroy(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_enrich=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        enrichment = models.Enrichment.objects.filter(pk=pk, corpus=corpus).get()
        if not enrichment.corpus.database.is_running:
            return Response("Database is not running, cannot delete enrichment",
                    status=status.HTTP_400_BAD_REQUEST)
        response = Response(True)
        task_id = delete_enrichment_task.delay(enrichment.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response


class QueryViewSet(viewsets.ModelViewSet):
    model = models.Query
    serializer_class = serializers.QuerySerializer

    def get_queryset(self):
        return models.Query.objects.filter(corpus__pk=self.kwargs['corpus_pk'])

    def list(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
            queries = models.Query.objects.filter(user=request.user, corpus=corpus).all()
        else:
            queries = models.Query.objects.filter(corpus=corpus).all()
        return Response(serializers.QuerySerializer(queries, many=True).data)

    def create(self, request, corpus_pk=None, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot create query",
                    status=status.HTTP_400_BAD_REQUEST)
        query = models.Query.objects.create(name=request.data['name'], user=request.user,
                                            annotation_type=request.data['annotation_type'][0].upper(), corpus=corpus)
        query.config = request.data
        response = Response(serializers.QuerySerializer(query).data)
        task_id = run_query_task.delay(query.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response

    def update(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot update query",
                    status=status.HTTP_400_BAD_REQUEST)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        refresh = request.data.pop('refresh', False)
        query.name = request.data.get('name')
        do_run = refresh or query.config['filters'] != request.data['filters'] or \
                 query.config['positions'] != request.data['positions']
        c = query.config
        c.update(request.data)
        query.config = c
        response = Response(serializers.QuerySerializer(query).data)
        if do_run:
            task_id = run_query_task.delay(query.pk)
            response["task"] = task_id.task_id
            time.sleep(1)
        return response

    @action(detail=False, methods=['GET'])
    def utterance(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queries = models.Query.objects.filter(user=request.user, corpus=corpus,annotation_type='U').filter(~Q(name='Bestiary query')).all()
        return Response(serializers.QuerySerializer(queries, many=True).data)

    @action(detail=False, methods=['GET'])
    def word(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queries = models.Query.objects.filter(user=request.user,annotation_type='W', corpus=corpus).all()
        return Response(serializers.QuerySerializer(queries, many=True).data)

    @action(detail=False, methods=['GET'])
    def syllable(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queries = models.Query.objects.filter(user=request.user,annotation_type='S', corpus=corpus).all()
        return Response(serializers.QuerySerializer(queries, many=True).data)

    @action(detail=False, methods=['GET'])
    def phone(self, request, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        queries = models.Query.objects.filter(user=request.user,annotation_type='P', corpus=corpus).all()
        return Response(serializers.QuerySerializer(queries, many=True).data)

    def retrieve(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        response = Response(serializers.QuerySerializer(query).data)
        if not query.running and query.result_count is None:
            task_id = run_query_task.delay(query.pk)
            response["task"] = task_id.task_id
            time.sleep(1)

        return response

    @action(detail=True, methods=['get'])
    def results(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None)
        ordering = request.query_params.get('ordering', '')
        offset = int(request.query_params.get('offset', 0))
        limit = int(request.query_params.get('limit', 100))
        results = query.get_results(ordering, limit, offset)
        resp = {'data': results, 'count': query.result_count}
        return Response(resp)

    @action(detail=True, methods=['put'])
    def ordering(self, request, pk=None, corpus_pk=None, index=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        config = query.config
        config['ordering'] = request.data.get('ordering')
        query.resort(config['ordering'])
        query.config = config

        return Response(serializers.QuerySerializer(query).data)

    @action(detail=True, methods=['get'])
    def result(self, request, pk=None, corpus_pk=None, index=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_view_detail=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None)
        ordering = request.query_params.get('ordering', '')
        index = int(request.query_params.get('index', '0'))
        limit = 1
        offset = index

        with_subannotations = bool(strtobool(request.query_params.get('with_subannotations', 'True')))
        result = query.get_results(ordering, limit, offset)[0]
        utterance_id = result['utterance']['current']['id']
        data = {'result': result}
        try:
            with CorpusContext(corpus.config) as c:
                q = c.query_graph(c.utterance)
                q = q.filter(c.utterance.id == utterance_id)
                q = q.preload(c.utterance.word)
                q = q.preload(c.utterance.syllable)
                q = q.preload(c.utterance.phone)
                q = q.preload(c.utterance.speaker)
                q = q.preload(c.utterance.discourse)
                if with_subannotations:
                    for t in c.hierarchy.annotation_types:
                        if t in c.hierarchy.subannotations:
                            for s in c.hierarchy.subannotations[t]:
                                if t == 'utterance':
                                    q = q.preload(getattr(c.utterance, s))
                                else:
                                    q = q.preload(getattr(getattr(c.utterance, t), s))
                acoustic_columns = c.hierarchy.acoustics
                acoustic_column_names = []
                for a_column in acoustic_columns:
                    acoustic = getattr(c.utterance, a_column)
                    acoustic.relative = True
                    acoustic = acoustic.track
                    q = q.preload_acoustics(acoustic)
                    acoustic_column_names.append(a_column)

                utterances = q.all()
                if utterances is None:
                    data['utterance'] = None
                elif not len(utterances):
                    return Response('The utterance IDs in this query look to be outdated. '
                                    'Please refresh the query.', status=status.HTTP_400_BAD_REQUEST)
                else:
                    serializer = serializers.serializer_factory(c.hierarchy, 'utterance',
                                                                acoustic_columns=acoustic_columns,
                                                                with_waveform=False,
                                                                with_spectrogram=False,
                                                                top_level=True,
                                                                with_lower_annotations=True, detail=True,
                                                                with_subannotations=True)
                    utt = utterances[0]
                    data['utterance'] = serializer(utt).data
        except neo4j_exceptions.ServiceUnavailable:
            return Response(None, status=status.HTTP_423_LOCKED)
        return Response(data)

    @action(detail=True, methods=['get'])
    def get_spectrogram(self, request, pk=None, corpus_pk=None, index=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_view_detail=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None)
        ordering = request.query_params.get('ordering', '')
        index = int(request.query_params.get('index', '0'))
        limit = 1
        offset = index
        result = query.get_results(ordering, limit, offset)[0]
        utterance_id = result['utterance']['current']['id']
        data = {'result': result}
        try:
            with CorpusContext(corpus.config) as c:
                q = c.query_graph(c.utterance)
                q = q.filter(c.utterance.id == utterance_id)
                utterances = q.all()
                if utterances is None:
                    data['spectogram'] = None
                elif not len(utterances):
                    return Response('The utterance IDs in this query look to be outdated. '
                                    'Please refresh the query.', status=status.HTTP_400_BAD_REQUEST)
                else:
                    data['spectrogram'] = utterances[0].spectrogram_fast
        except neo4j_exceptions.ServiceUnavailable:
            return Response(None, status=status.HTTP_423_LOCKED)
        return Response(data)

    @action(detail=True, methods=['get'])
    def get_waveform(self, request, pk=None, corpus_pk=None, index=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_view_detail=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None)
        ordering = request.query_params.get('ordering', '')
        index = int(request.query_params.get('index', '0'))
        limit = 1
        offset = index
        result = query.get_results(ordering, limit, offset)[0]
        utterance_id = result['utterance']['current']['id']
        data = {'result': result}
        try:
            with CorpusContext(corpus.config) as c:
                q = c.query_graph(c.utterance)
                q = q.filter(c.utterance.id == utterance_id)
                utterances = q.all()
                if utterances is None:
                    data['waveform'] = None
                elif not len(utterances):
                    return Response('The utterance IDs in this query look to be outdated. '
                                    'Please refresh the query.', status=status.HTTP_400_BAD_REQUEST)
                else:
                    data['waveform'] = utterances[0].waveform
        except neo4j_exceptions.ServiceUnavailable:
            return Response(None, status=status.HTTP_423_LOCKED)
        return Response(data)

    @action(detail=True, methods=['post'])
    def commit_subannotation_changes(self, request, pk=None, corpus_pk=None, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True, can_edit=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        with CorpusContext(corpus.config) as c:
            for annotation_type, subannotation_dict in request.data.items():
                for subannotation, tokens in subannotation_dict.items():

                    #Get rid of helper properties used by the JS front end.
                    excluded_properties = ["parent_id", "annotation_type", "subannotation", "id"]

                    data = [{"id": t["id"],
                            "props": {k:v for k, v in t.items() if k not in excluded_properties}}
                            for t in tokens]

                    #Find any new properties not yet encoded
                    props_to_add = []
                    for prop, val in data[0]["props"].items():
                        #This assumes all tokens have identical properties
                        if not c.hierarchy.has_subannotation_property(subannotation, prop):
                            props_to_add.append((prop, type(val)))

                    if props_to_add:
                        c.hierarchy.add_subannotation_properties(c,subannotation, props_to_add)
                        c.encode_hierarchy()
                        for prop, val in props_to_add:
                            #Set default value for all subannotations of this type
                            default = None
                            if val == bool:
                                default = False
                            elif val == str:
                                default = '""'
                            elif val == int:
                                default = 0
                            elif val == float:
                                default = 0.0

                            statement = """
                            MATCH (n:{subannotation}:{corpus_name})
                            SET n.{prop} = {default}
                            """.format(subannotation=subannotation, corpus_name=c.cypher_safe_name, 
                                    prop=prop, default=default)
                            c.execute_cypher(statement)


                    statement = """
                    UNWIND {{data}} as d
                    MERGE (n:{subannotation}:{corpus_name} {{id: d.id}})
                    SET n += d.props
                    """.format(subannotation=subannotation, corpus_name=c.cypher_safe_name)
                    resp = c.execute_cypher(statement, data=data).value()
        return Response(resp)

    @action(detail=True, methods=['post'])
    def generate_subset(self, request, pk=None, corpus_pk=None, *args, **kwargs):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.data.get('subset_name', ''):
            return Response(
                'The subset must have a name.',
                status=status.HTTP_400_BAD_REQUEST)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot generate subset",
                    status=status.HTTP_400_BAD_REQUEST)
        with CorpusContext(corpus.config) as g:
            if g.hierarchy.has_token_subset(request.data.get('annotation_type', ''), request.data.get('subset_name', '')) or \
                    g.hierarchy.has_type_subset(request.data.get('annotation_type', ''), request.data.get('subset_name', '')):
                return Response("There is already a subset with the name, {}".format(request.data.get('subset_name', '')), 
                        status=status.HTTP_400_BAD_REQUEST)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None, status=status.HTTP_423_LOCKED)
        c = query.config
        c.update(request.data)
        query.config = c
        response = Response(serializers.QuerySerializer(query).data)
        task_id = run_query_generate_subset_task.delay(query.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response


    @action(detail=True, methods=['post'])
    def generate_export(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot export",
                    status=status.HTTP_400_BAD_REQUEST)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None, status=status.HTTP_423_LOCKED)
        c = query.config
        c.update(request.data)
        query.config = c
        response = Response(serializers.QuerySerializer(query).data)
        task_id = run_query_export_task.delay(query.pk)
        response["task"] = task_id.task_id
        time.sleep(1)
        return response

    @action(detail=True, methods=['get'])
    def get_export_csv(self, request, pk=None, corpus_pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        corpus = models.Corpus.objects.get(pk=corpus_pk)
        permissions = corpus.user_permissions.filter(user=request.user, can_query=True).all()
        if not len(permissions):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not corpus.database.is_running:
            return Response("Database is not running, cannot export",
                    status=status.HTTP_400_BAD_REQUEST)
        query = models.Query.objects.filter(pk=pk, corpus=corpus).get()
        if query is None:
            return Response(None, status=status.HTTP_400_BAD_REQUEST)
        if query.running:
            return Response(None, status=status.HTTP_423_LOCKED)
        if os.path.exists(query.export_path):
            with open(query.export_path, 'rb') as fh:
                response = HttpResponse(fh.read(), content_type='text/csv')
                response['Content-Disposition'] = 'attachment; filename="{}"'.format(
                    os.path.basename(query.export_path))
                return response
        return Response(None, status=status.HTTP_400_BAD_REQUEST)

class SpadeScriptViewSet(viewsets.ViewSet):
    def list(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        scripts = models.SpadeScript.objects.all()
        return Response(serializers.SpadeScriptSerializer(scripts, many=True).data)

    @action(detail=False, methods=['get'])
    def is_enabled(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return Response(settings.SPADE_SCRIPTS_ENABLED)


    @action(detail=False, methods=['get'])
    def list_scripts(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        scripts = list(filter(lambda x: x.endswith(".py"), \
                os.listdir(settings.SPADE_SCRIPT_DIRECTORY)))
        return Response(scripts)

    @action(detail=False, methods=['post'])
    def list_csvs(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        target = request.data["target_corpus"]
        return Response(list(filter(lambda x: x.endswith(".csv"), \
                os.listdir(os.path.join(settings.SPADE_SCRIPT_DIRECTORY, target)))))

    @action(detail=False, methods=['get'])
    def list_corpora(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        return Response(list(filter( \
            lambda x: os.path.isdir(os.path.join(settings.SPADE_SCRIPT_DIRECTORY, x))
                and not x in ["Common", ".git"], \
                os.listdir(settings.SPADE_SCRIPT_DIRECTORY))))

    @action(detail=False, methods=['post'])
    def download_csv(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        target = request.data["target_corpus"]
        csv_file = request.data["csv_file"]
        csvs = list(filter(lambda x: x.endswith(".csv"), \
                os.listdir(os.path.join(settings.SPADE_SCRIPT_DIRECTORY, target))))
        if csv_file not in csvs:
            return Response("{} is not a valid file".format(csv_file), 
                    status=status.HTTP_400_BAD_REQUEST)
        path = os.path.join(settings.SPADE_SCRIPT_DIRECTORY, target, csv_file)
        with open(path, 'rb') as fh:
            response = HttpResponse(fh.read(), content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="{}"'.format(
                csv_file)
            return response
        return Response("That CSV file does not exist", status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'])
    def get_log(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        script = models.SpadeScript.objects.get(pk=pk)
        with open(script.log_path, 'r') as f:
            output = f.read()
        return Response(output)

    @action(detail=False, methods=['post'])
    def run_script(self, request):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        if not request.user.is_superuser and not request.user.profile.user_type == "U":
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        script = request.data["script"]
        target = request.data["target_corpus"]
        reset = request.data["reset"]
        if type(reset) == str:
            reset = strtobool(reset)

        if script not in list(filter(lambda x: x.endswith(".py"), \
                os.listdir(settings.SPADE_SCRIPT_DIRECTORY))):
            return Response("{} is not a valid script".format(script), status=status.HTTP_400_BAD_REQUEST)
        if target not in list(filter(lambda x: os.path.isdir(os.path.join(settings.SPADE_SCRIPT_DIRECTORY, x)) and not x in ["Common", ".git"], \
                os.listdir(settings.SPADE_SCRIPT_DIRECTORY))):
            return Response("{} is not a valid corpus".format(target), status=status.HTTP_400_BAD_REQUEST)
        task_id = run_spade_script_task.delay(script, target, reset)
        response = Response(True)
        response["task"] = task_id.task_id
        return response

class TaskViewSet(viewsets.ViewSet):

    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        task = models.BackgroundTask.objects.get(pk=pk)
        return Response(task.status())

    @action(detail=True, methods=['get'])
    def failed(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        task = models.BackgroundTask.objects.get(pk=pk)
        return Response(task.failed)

    @action(detail=True, methods=['get'])
    def finished(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        task = models.BackgroundTask.objects.get(pk=pk)
        return Response(not task.running)

    @action(detail=True, methods=['get'])
    def exceptions(self, request, pk=None):
        if isinstance(request.user, django.contrib.auth.models.AnonymousUser):
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        task = models.BackgroundTask.objects.get(pk=pk)
        if task.failed:
            e = task.get_exceptions()
            return Response({"name":task.name,
                "message":{"status":500,
                    "statusText":type(e).__name__,
                    "data":getattr(e, "message", str(e))}
                })
        return Response("This task did not fail")
