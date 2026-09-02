# Migration for the Blog AI writing assistant feature: adds SEO/tags fields
# to Blog, plus BlogDraftSession/BlogDraftMessage (the persisted AI chat
# sessions used by marketing.ai / marketing.views.BlogAIStreamView).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('marketing', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='blog',
            name='meta_title',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='blog',
            name='meta_description',
            field=models.CharField(blank=True, max_length=320),
        ),
        migrations.AddField(
            model_name='blog',
            name='tags',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='BlogDraftSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('topic', models.CharField(blank=True, max_length=255)),
                ('target_audience', models.CharField(blank=True, max_length=255)),
                ('tone', models.CharField(blank=True, max_length=50)),
                ('word_count_target', models.PositiveIntegerField(blank=True, null=True)),
                ('flavor_refs', models.JSONField(blank=True, default=list)),
                ('status', models.CharField(choices=[('active', 'Active'), ('applied', 'Applied to blog'), ('abandoned', 'Abandoned')], default='active', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('blog', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ai_sessions', to='marketing.blog')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'blog_draft_sessions',
                'ordering': ['-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='BlogDraftMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(choices=[('admin', 'Admin'), ('assistant', 'Assistant'), ('system', 'System')], max_length=10)),
                ('action', models.CharField(choices=[('generate', 'Generate draft'), ('rewrite', 'Rewrite/improve'), ('suggest_meta', 'Suggest title/excerpt/meta'), ('outline', 'Outline'), ('chat', 'Chat/feedback')], default='chat', max_length=15)),
                ('content', models.TextField(blank=True)),
                ('provider', models.CharField(blank=True, max_length=30)),
                ('model', models.CharField(blank=True, max_length=80)),
                ('prompt_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('completion_tokens', models.PositiveIntegerField(blank=True, null=True)),
                ('cost_usd', models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='messages', to='marketing.blogdraftsession')),
            ],
            options={
                'db_table': 'blog_draft_messages',
                'ordering': ['created_at', 'id'],
            },
        ),
    ]
