import logging
import random
from datetime import datetime, timedelta
from typing import Type, List, Dict, Any

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import models, transaction
from django.db.models.fields import Field
from django.db.models.fields.related import ForeignKey, ManyToManyField
from django.contrib.auth.models import User, Group
from django.utils import timezone
from faker import Faker

logger = logging.getLogger(__name__)
fake = Faker()

class Command(BaseCommand):
    help = 'Generates 100 dummy records for all models in the application'

    def __init__(self):
        super().__init__()
        self.created_objects: Dict[str, List[Any]] = {}
        self.processed_models = set()
        self.skipped_models = set()
        self.faker = Faker(['en_US'])  # Initialize with specific locale

    def add_arguments(self, parser):
        parser.add_argument(
            '--exclude',
            nargs='+',
            type=str,
            help='Models to exclude (format: app_label.model_name)'
        )

    def handle(self, *args, **options):
        excluded_models = options.get('exclude', [])
        
        try:
            # Create base auth models first
            self.create_base_auth_models()
            
            # Get all models from all installed apps
            all_models = apps.get_models()
            
            # Filter out excluded models and auth models
            models_to_process = [
                model for model in all_models 
                if (f"{model._meta.app_label}.{model._meta.model_name}" not in excluded_models and
                    model._meta.app_label != 'auth')
            ]

            # Sort models by dependencies
            sorted_models = self.sort_models_by_dependencies(models_to_process)

            # Process each model without transaction to avoid rollback on error
            for model in sorted_models:
                try:
                    self.generate_data_for_model(model)
                    # Handle M2M relations after successful creation
                    self.handle_many_to_many_relations(model)
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(
                            f'Error processing {model._meta.label}: {str(e)}'
                        )
                    )

            self.stdout.write(
                self.style.SUCCESS(
                    f'Successfully generated data for {len(self.processed_models)} models'
                )
            )
            
            if self.skipped_models:
                self.stdout.write(
                    self.style.WARNING(
                        f'Skipped models: {", ".join(self.skipped_models)}'
                    )
                )

        except Exception as e:
            logger.error(f'Error generating dummy data: {str(e)}')
            self.stdout.write(
                self.style.ERROR(f'Failed to generate dummy data: {str(e)}')
            )

    def create_base_auth_models(self):
        """Create necessary auth models first"""
        try:
            # Create groups
            groups = ['managers', 'operators', 'superoperators']
            for group_name in groups:
                Group.objects.get_or_create(name=group_name)

            # Create superuser if doesn't exist
            if not User.objects.filter(is_superuser=True).exists():
                User.objects.create_superuser(
                    'admin', 'admin@example.com', 'admin'
                )

        except Exception as e:
            logger.error(f'Error creating base auth models: {str(e)}')

    def generate_data_for_model(self, model: Type[models.Model]) -> None:
        """Generate dummy data for a specific model."""
        model_name = f"{model._meta.app_label}.{model._meta.model_name}"
        
        try:
            self.stdout.write(f'Generating data for {model_name}...')
            
            # Skip abstract models
            if model._meta.abstract:
                self.skipped_models.add(f"{model_name} (abstract)")
                return

            # Create objects one by one to handle errors
            created_objects = []
            for i in range(100):
                try:
                    obj = self.create_model_instance(model)
                    if obj:
                        obj.save()
                        created_objects.append(obj)
                except Exception as e:
                    logger.error(f'Error creating instance {i} of {model_name}: {str(e)}')
                    continue
                
            self.processed_models.add(model_name)
            self.created_objects[model_name] = created_objects
            
        except Exception as e:
            logger.error(f'Error generating data for {model_name}: {str(e)}')
            self.skipped_models.add(model_name)

    def generate_field_value(self, field: Field) -> Any:
        """Generate appropriate dummy value for a given field."""
        try:
            if isinstance(field, models.CharField):
                max_length = field.max_length or 100
                
                if field.name.lower().endswith('email'):
                    return self.faker.email()
                elif field.name.lower().endswith('phone'):
                    return self.faker.phone_number()[:max_length]
                elif field.name.lower().endswith('name'):
                    return self.faker.name()[:max_length]
                elif field.name.lower() == 'language_code':
                    return 'en'
                else:
                    return self.faker.text(max_length)[:max_length]
                
            elif isinstance(field, models.TextField):
                return self.faker.text()
                
            elif isinstance(field, models.DateTimeField):
                if field.name in ['created', 'modified', 'creation_date']:
                    return timezone.now()
                return self.faker.date_time_this_decade(tzinfo=timezone.get_current_timezone())
                
            elif isinstance(field, models.DateField):
                if field.name in ['created', 'modified', 'creation_date']:
                    return timezone.now().date()
                return self.faker.date_this_decade()
                
            elif isinstance(field, models.BooleanField):
                return self.faker.boolean()
                
            elif isinstance(field, (models.IntegerField, models.SmallIntegerField)):
                if field.name == 'index_number':
                    return random.randint(1, 100)
                return self.faker.random_int(min=0, max=1000)
                
            elif isinstance(field, models.DecimalField):
                return self.faker.pydecimal(
                    left_digits=field.max_digits - field.decimal_places,
                    right_digits=field.decimal_places,
                    positive=True
                )
                
            elif isinstance(field, models.URLField):
                return self.faker.url()
                
            elif isinstance(field, ForeignKey):
                related_model = field.remote_field.model
                related_model_name = f"{related_model._meta.app_label}.{related_model._meta.model_name}"
                
                # Try to get from created objects first
                related_objects = self.created_objects.get(related_model_name, [])
                if related_objects:
                    return random.choice(related_objects)
                
                # Try to get from database
                try:
                    return related_model.objects.order_by('?').first()
                except Exception:
                    return None
                
            elif isinstance(field, models.EmailField):
                return self.faker.email()
                
        except Exception as e:
            logger.error(f'Error generating value for field {field.name}: {str(e)}')
            return None
            
        return None

    def create_model_instance(self, model: Type[models.Model]) -> models.Model:
        """Create a single instance of a model with dummy data."""
        instance = model()
        
        for field in model._meta.fields:
            if field.name in ('id', 'pk'):
                continue
                
            value = self.generate_field_value(field)
            setattr(instance, field.name, value)
            
        return instance

    def handle_many_to_many_relations(self, model: Type[models.Model]) -> None:
        """Handle M2M relationships after initial object creation."""
        for field in model._meta.many_to_many:
            related_model = field.remote_field.model
            related_objects = self.created_objects.get(
                f"{related_model._meta.app_label}.{related_model._meta.model_name}", []
            )
            
            if not related_objects:
                continue
                
            for obj in self.created_objects.get(
                f"{model._meta.app_label}.{model._meta.model_name}", []
            ):
                num_relations = random.randint(1, min(5, len(related_objects)))
                related = random.sample(related_objects, num_relations)
                getattr(obj, field.name).set(related)