from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import JSONField
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone


class Profile(models.Model):
    # Link to the auth user, since we're basically just extending it
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    labeled_data = models.ManyToManyField(
        "Data", related_name="labelers", through="DataLabel"
    )

    def __str__(self):
        return self.user.username


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def save_user(sender, instance, **kwargs):
    instance.profile.save()


class Project(models.Model):
    class Meta:
        indexes = [models.Index(fields=["id"])]

    name = models.TextField()
    description = models.TextField(blank=True)
    creator = models.ForeignKey("Profile", on_delete=models.CASCADE)
    percentage_irr = models.FloatField(
        default=10.0, validators=[MinValueValidator(0.0), MaxValueValidator(100.0)]
    )
    num_users_irr = models.IntegerField(default=2, validators=[MinValueValidator(2)])
    codebook_file = models.TextField(default="")
    batch_size = models.IntegerField(default=30)
    allow_coders_view_labels = models.BooleanField(default=False)
    umbrella_string = models.TextField(blank=True)
    """ Advanced options """
    # the current options are 'random', 'least confident', 'entropy', and 'margin sampling'
    ACTIVE_L_CHOICES = [
        ("least confident", "By Uncertainty using Least Confident"),
        ("margin sampling", "By Uncertainty using the Margin"),
        ("entropy", "By Uncertainty using Entropy"),
        ("random", "Randomly (No Active Learning)"),
    ]

    CLASSIFIER_CHOICES = [
        ("logistic regression", "Logistic Regression (default)"),
        ("svm", "Support Vector Machine (warning: slower for large datasets)"),
        ("random forest", "Random Forest"),
        ("gnb", "Gaussian Naive Bayes"),
    ]

    learning_method = models.CharField(
        max_length=15, default="least confident", choices=ACTIVE_L_CHOICES
    )
    classifier = models.CharField(
        max_length=19,
        default="logistic regression",
        choices=CLASSIFIER_CHOICES,
        null=True,
    )

    DEDUP_CHOICES = (
        ("Text", "Text only"),
        ("Metadata_Text", "Text and all Metadata fields"),
        ("Text_Some_Metadata", "Text and selected Metadata fields"),
    )
    dedup_on = models.CharField(
        max_length=19,
        default="Text",
        choices=DEDUP_CHOICES,
        null=False,
    )

    dedup_fields = models.CharField(
        max_length=50,
        default="",
        null=True,
    )

    def get_absolute_url(self):
        return reverse("projects:project_detail", kwargs={"pk": self.pk})

    def get_current_training_set(self):
        try:
            return self.trainingset_set.all().order_by("-set_number")[0]
        except IndexError:
            return None

    def admin_count(self):
        return self.projectpermissions_set.all().filter(permission="ADMIN").count()

    def coder_count(self):
        return self.projectpermissions_set.all().filter(permission="CODER").count()

    def labeled_data_count(self):
        return self.data_set.all().filter(datalabel__isnull=False).count()

    def unverified_labeled_data_count(self):
        return (
            self.data_set.all()
            .filter(datalabel__isnull=False, datalabel__verified__isnull=False)
            .count()
        )

    def has_model(self):
        if self.model_set.count() > 0:
            return True
        else:
            return False

    def has_database_connection(self):
        return self.externaldatabase.env_file != ""

    def get_ingest_database(self):
        if self.externaldatabase.has_ingest:
            return f"{self.externaldatabase.ingest_schema}.{self.externaldatabase.ingest_table_name}"
        else:
            return ""

    def get_scheduled_ingest(self):
        if self.externaldatabase.has_ingest:
            if self.externaldatabase.cron_ingest:
                return "On"
            else:
                return "Off"
        else:
            return "NaN"

    def get_scheduled_export(self):
        if self.externaldatabase.has_export:
            if self.externaldatabase.cron_export:
                return "On"
            else:
                return "Off"
        else:
            return "NaN"

    def get_export_verified_only(self):
        if self.externaldatabase.has_export:
            if self.externaldatabase.export_verified_only:
                return "On"
            else:
                return "Off"
        else:
            return "NaN"

    def get_export_database(self):
        if self.externaldatabase.has_export:
            return f"{self.externaldatabase.export_schema}.{self.externaldatabase.export_table_name}"
        else:
            return ""


class ProjectPermissions(models.Model):
    class Meta:
        unique_together = ("profile", "project")

    PERM_CHOICES = (
        ("ADMIN", "Admin"),
        ("CODER", "Coder"),
    )
    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    permission = models.CharField(max_length=5, choices=PERM_CHOICES)


class Model(models.Model):
    pickle_path = models.TextField()
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    training_set = models.ForeignKey("TrainingSet", on_delete=models.CASCADE)
    cv_accuracy = models.FloatField()
    cv_metrics = JSONField()
    predictions = models.ManyToManyField(
        "Data", related_name="models", through="DataPrediction"
    )


class Data(models.Model):
    class Meta:
        unique_together = ("hash", "upload_id_hash", "project")
        indexes = [models.Index(fields=["project"])]

    text = models.TextField()
    hash = models.CharField(max_length=128)
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    irr_ind = models.BooleanField(default=False)
    upload_id = models.CharField(max_length=128)
    upload_id_hash = models.CharField(max_length=128)

    def __str__(self):
        return self.text


class MetaDataField(models.Model):
    project = models.ForeignKey(
        "Project", related_name="metadatafields", on_delete=models.CASCADE
    )
    field_name = models.TextField()
    use_with_dedup = models.BooleanField(default=True)

    def __str__(self):
        return self.field_name


class MetaData(models.Model):
    class Meta:
        unique_together = ("data", "metadata_field")

    data = models.ForeignKey("Data", on_delete=models.CASCADE, related_name="metadata")
    metadata_field = models.ForeignKey("MetaDataField", on_delete=models.CASCADE)
    value = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{str(self.metadata_field)}: {self.value}"


class ExternalDatabase(models.Model):
    project = models.OneToOneField(
        "Project", related_name="externaldatabase", on_delete=models.CASCADE
    )
    env_file = models.TextField()
    DB_TYPE_CHOICES = (
        ("none", "No Database Connection"),
        ("microsoft", "MS SQL"),
    )
    database_type = models.CharField(
        max_length=9,
        default="none",
        choices=DB_TYPE_CHOICES,
        null=False,
    )

    has_ingest = models.BooleanField(default=False)
    cron_ingest = models.BooleanField(default=False)
    ingest_schema = models.CharField(max_length=50, null=True)
    ingest_table_name = models.CharField(max_length=50, null=True)
    has_export = models.BooleanField(default=False)
    cron_export = models.BooleanField(default=False)
    export_schema = models.CharField(max_length=1024, null=True)
    export_table_name = models.CharField(max_length=1024, null=True)
    export_verified_only = models.BooleanField(default=False)


class LabelEmbeddings(models.Model):
    class Meta:
        ordering = ("label_id",)

    label = models.OneToOneField(
        "Label", on_delete=models.CASCADE, related_name="labelEmbedding"
    )
    embedding = ArrayField(models.FloatField())


class Label(models.Model):
    class Meta:
        ordering = ("id",)
        unique_together = ("name", "project")

    name = models.TextField()
    project = models.ForeignKey(
        "Project", related_name="labels", on_delete=models.CASCADE
    )
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name


class IRRLog(models.Model):
    class Meta:
        unique_together = ("data", "profile")

    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    label = models.ForeignKey("Label", null=True, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(null=True, default=None)


class DataLabel(models.Model):
    class Meta:
        unique_together = ("data", "profile")

    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    label = models.ForeignKey("Label", on_delete=models.CASCADE)
    training_set = models.ForeignKey("TrainingSet", on_delete=models.CASCADE)
    time_to_label = models.IntegerField(null=True)
    timestamp = models.DateTimeField(null=True, default=None)
    pre_loaded = models.BooleanField(default=False)


class VerifiedDataLabel(models.Model):
    data_label = models.OneToOneField(
        "DataLabel", on_delete=models.CASCADE, primary_key=True, related_name="verified"
    )
    verified_by = models.ForeignKey("Profile", on_delete=models.CASCADE)
    verified_timestamp = models.DateTimeField(null=True, default=None)

    def __str__(self):
        return str(self.verified_by)


class LabelChangeLog(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    old_label = models.TextField()
    new_label = models.TextField()
    change_timestamp = models.DateTimeField(null=True, default=None)


class DataPrediction(models.Model):
    class Meta:
        unique_together = ("data", "model", "label")

    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    model = models.ForeignKey("Model", on_delete=models.CASCADE)
    label = models.ForeignKey("Label", on_delete=models.CASCADE)
    predicted_probability = models.FloatField()


class DataUncertainty(models.Model):
    class Meta:
        unique_together = ("data", "model")

    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    model = models.ForeignKey("Model", on_delete=models.CASCADE)
    least_confident = models.FloatField()
    margin_sampling = models.FloatField()
    entropy = models.FloatField()


class Queue(models.Model):
    profile = models.ForeignKey(
        "Profile", blank=True, null=True, on_delete=models.CASCADE
    )
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    QUEUE_TYPES = (("admin", "Admin"), ("irr", "IRR"), ("normal", "Normal"))
    type = models.CharField(max_length=6, default="normal", choices=QUEUE_TYPES)
    length = models.IntegerField()
    data = models.ManyToManyField("Data", related_name="queues", through="DataQueue")


class DataQueue(models.Model):
    class Meta:
        unique_together = ("queue", "data")

    queue = models.ForeignKey("Queue", on_delete=models.CASCADE)
    data = models.ForeignKey("Data", on_delete=models.CASCADE)


class AssignedData(models.Model):
    class Meta:
        unique_together = ("profile", "queue", "data")

    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    queue = models.ForeignKey("Queue", on_delete=models.CASCADE)
    assigned_timestamp = models.DateTimeField(default=timezone.now)


class TrainingSet(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    set_number = models.IntegerField()
    celery_task_id = models.TextField(blank=True)


class RecycleBin(models.Model):
    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=timezone.now)


class AdminProgress(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    profile = models.ForeignKey("Profile", on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=timezone.now)
    last_action = models.DateTimeField(auto_now=True)


class AdjudicateDescription(models.Model):
    project = models.ForeignKey("Project", on_delete=models.CASCADE)
    data = models.ForeignKey("Data", on_delete=models.CASCADE)
    message = models.TextField()
    isResolved = models.BooleanField(default=False)
