import pandas as pd
from django import forms
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView, TemplateView, View
from django.views.generic.edit import DeleteView, UpdateView
from formtools.wizard.views import SessionWizardView
from smart.settings import PROJECT_SUGGESTION_MAX

from core.forms import (
    AdvancedWizardForm,
    CodeBookWizardForm,
    DataUpdateWizardForm,
    DataWizardForm,
    ExternalDatabaseWizardForm,
    LabelDescriptionFormSet,
    LabelFormSet,
    PermissionsFormSet,
    ProjectUpdateOverviewForm,
    ProjectUpdateAdvancedForm,
    ProjectWizardForm,
)
from core.models import (
    AssignedData,
    Data,
    ExternalDatabase,
    Label,
    MetaDataField,
    Project,
    TrainingSet,
)
from core.templatetags import project_extras
from core.utils.util import (
    get_projects,
    get_projects_umbrellas,
    project_status,
    save_codebook_file,
    update_label_embeddings,
    upload_data,
)
from core.utils.utils_annotate import batch_unassign, leave_coding_page
from core.utils.utils_external_db import delete_external_db_file, save_external_db_file
from core.utils.utils_queue import add_queue, find_queue_length


# Projects
class ProjectCode(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "smart/smart.html"
    permission_denied_message = (
        "You must have permissions to access the coding page for this project."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile) > 0
        )

    def get_context_data(self, **kwargs):
        ctx = super(ProjectCode, self).get_context_data(**kwargs)
        project = Project.objects.get(pk=self.kwargs["pk"])
        ctx["pk"] = self.kwargs["pk"]
        admin = (
            project_extras.proj_permission_level(project, self.request.user.profile) > 1
        )
        if admin:
            ctx["admin"] = "true"
        else:
            ctx["admin"] = "false"
        ctx["project"] = Project.objects.get(pk=self.kwargs["pk"])

        uses_irr = ctx["project"].percentage_irr > 0
        if uses_irr:
            ctx["project_uses_irr"] = "true"
        else:
            ctx["project_uses_irr"] = "false"

        ctx["project_suggestion_max"] = PROJECT_SUGGESTION_MAX

        return ctx


class ProjectAdmin(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "projects/admin/admin.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Admin page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_context_data(self, **kwargs):
        ctx = super(ProjectAdmin, self).get_context_data(**kwargs)

        project = Project.objects.get(pk=self.kwargs["pk"])
        ctx["project"] = project
        users = AssignedData.objects.filter(data__project=project).distinct("profile")
        coders = [d.profile for d in users]
        ctx["coders"] = coders
        return ctx


class ProjectList(LoginRequiredMixin, ListView):
    model = Project
    template_name = "projects/list.html"
    ordering = "name"

    def get_queryset(self):
        projects = get_projects(self, True)
        for project in projects:
            project_details = project_status(project)
            leave_coding_page(self.request.user.profile, project)
            project.project_details = project_details

        return projects


class ProjectDetail(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = Project
    template_name = "projects/detail.html"
    permission_denied_message = "You must have permissions to access this project page."
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile) > 0
        )


class ProjectCreateWizard(LoginRequiredMixin, SessionWizardView):
    file_storage = FileSystemStorage(location=settings.DATA_DIR)
    form_list = [
        ("project", ProjectWizardForm),
        ("labels", LabelFormSet),
        ("permissions", PermissionsFormSet),
        ("codebook", CodeBookWizardForm),
        ("external", ExternalDatabaseWizardForm),
        ("data", DataWizardForm),
        ("advanced", AdvancedWizardForm),
    ]
    template_list = {
        "project": "projects/create/create_wizard_overview.html",
        "labels": "projects/create/create_wizard_labels.html",
        "permissions": "projects/create/create_wizard_permissions.html",
        "codebook": "projects/create/create_wizard_codebook.html",
        "external": "projects/create/create_wizard_external_db.html",
        "data": "projects/create/create_wizard_data.html",
        "advanced": "projects/create/create_wizard_advanced.html",
    }

    def get_template_names(self):
        return self.template_list[self.steps.current]

    def get_form_kwargs(self, step):
        kwargs = {}
        if step == "data":
            temp = []
            for label in self.get_cleaned_data_for_step("labels"):
                if "name" in label.keys():
                    temp.append(label["name"])
            kwargs["labels"] = temp
            external_data = self.get_cleaned_data_for_step("external")
            if "engine_database" in external_data:
                kwargs["database_type"] = external_data["database_type"]
                kwargs["engine_database"] = external_data["engine_database"]
                kwargs["ingest_table_name"] = external_data["ingest_table_name"]
                kwargs["ingest_schema"] = external_data["ingest_schema"]
            else:
                kwargs["engine_database"] = None

        return kwargs

    def get_form_kwargs_special(self, step=None):
        form_kwargs = {}

        if step == "permissions":
            form_kwargs.update(
                {"action": "create", "profile": self.request.user.profile}
            )

        return form_kwargs

    def get_form_prefix(self, step=None, form=None):
        prefix = ""

        if step == "labels":
            prefix = "label_set"
        if step == "permissions":
            prefix = "permission_set"
        if step == "advanced":
            prefix = "advanced"

        return prefix

    def get_context_data(self, form, step=None, **kwargs):
        context = super(ProjectCreateWizard, self).get_context_data(form=form, **kwargs)

        if self.steps.current == "project":
            projects_umbrellas = get_projects_umbrellas(self)
            context["umbrella_choices"] = [
                umbrella_choice
                for umbrella_choice in projects_umbrellas
                if umbrella_choice
            ]

        return context

    def get_form(self, step=None, data=None, files=None):
        """Overriding get_form.

        All the code is exactly the same except the if statement by the return.
        InlineLabelFormsets do not allow kwargs to be added to them through the
        traditional method of adding the kwargs to their init method.  Instead they must
        be passed using the `form_kwargs` parameter.  So If the step is a inline formset
        pass those special kwargs
        """
        if step is None:
            step = self.steps.current
        form_class = self.form_list[step]

        # prepare the kwargs for the form instance.
        kwargs = self.get_form_kwargs(step)
        kwargs.update(
            {
                "data": data,
                "files": files,
                "prefix": self.get_form_prefix(step, form_class),
                "initial": self.get_form_initial(step),
            }
        )
        if issubclass(form_class, (forms.ModelForm, forms.models.BaseInlineFormSet)):
            # If the form is based on ModelForm or InlineFormSet,
            # add instance if available and not previously set.
            kwargs.setdefault("instance", self.get_form_instance(step))
        elif issubclass(form_class, forms.models.BaseModelFormSet):
            # If the form is based on ModelFormSet, add queryset if available
            # and not previous set.
            kwargs.setdefault("queryset", self.get_form_instance(step))

        if step == "permissions":
            return form_class(**kwargs, form_kwargs=self.get_form_kwargs_special(step))
        else:
            return form_class(**kwargs)

    def done(self, form_list, form_dict, **kwargs):
        proj = form_dict["project"]
        labels = form_dict["labels"]
        permissions = form_dict["permissions"]
        advanced = form_dict["advanced"]
        external = form_dict["external"]
        data = form_dict["data"]
        codebook_data = form_dict["codebook"]

        with transaction.atomic():
            # Project
            proj_obj = proj.save(commit=False)
            advanced_data = advanced.cleaned_data

            proj_obj.creator = self.request.user.profile
            # Advanced Options
            proj_obj.save()
            proj_pk = proj_obj.pk
            # Save the codebook file

            cb_data = codebook_data.cleaned_data["data"]
            if cb_data != "":
                cb_filepath = save_codebook_file(cb_data, proj_pk)
            else:
                cb_filepath = ""
            proj_obj.codebook_file = cb_filepath

            external_data = external.cleaned_data
            if external_data["database_type"] != "none":
                connection_dict = {
                    k: v
                    for k, v in external_data.items()
                    if k in ["username", "password", "host", "port", "dbname", "driver"]
                }
                external_file = save_external_db_file(connection_dict, proj_pk)
                ExternalDatabase.objects.create(
                    project=proj_obj,
                    env_file=external_file,
                    database_type=external_data["database_type"],
                    has_ingest=external_data["has_ingest"],
                    cron_ingest=external_data["cron_ingest"],
                    ingest_schema=external_data["ingest_schema"],
                    ingest_table_name=external_data["ingest_table_name"],
                    has_export=external_data["has_export"],
                    cron_export=external_data["cron_export"],
                    export_schema=external_data["export_schema"],
                    export_table_name=external_data["export_table_name"],
                    export_verified_only=external_data["export_verified_only"],
                )
            else:
                # Create an empty database object
                ExternalDatabase.objects.create(
                    project=proj_obj, env_file="", database_type="none"
                )

            if advanced_data["batch_size"] == 0:
                batch_size = 10 * len(
                    [
                        x
                        for x in labels
                        if x.cleaned_data != {} and not x.cleaned_data["DELETE"]
                    ]
                )
            else:
                batch_size = advanced_data["batch_size"]

            proj_obj.batch_size = batch_size
            proj_obj.learning_method = advanced_data["learning_method"]
            proj_obj.percentage_irr = advanced_data["percentage_irr"]
            proj_obj.num_users_irr = advanced_data["num_users_irr"]
            proj_obj.classifier = advanced_data["classifier"]
            proj_obj.allow_coders_view_labels = advanced_data[
                "allow_coders_view_labels"
            ]

            # use the data dedup choice to set dedup property of metadata fields
            proj_obj.dedup_on = data.cleaned_data["dedup_on"]
            proj_obj.dedup_fields = data.cleaned_data["dedup_fields"]

            proj_obj.save()

            # Training Set
            TrainingSet.objects.create(project=proj_obj, set_number=0)

            # Labels
            labels.instance = proj_obj
            labels.save()

            # Permissions
            permissions.instance = proj_obj
            permissions.save()

            # Queue

            num_coders = (
                len(
                    [
                        x
                        for x in permissions
                        if x.cleaned_data != {} and not x.cleaned_data["DELETE"]
                    ]
                )
                + 1
            )
            q_length = find_queue_length(batch_size, num_coders)

            queue = add_queue(project=proj_obj, length=q_length)

            # Data
            f_data = data.cleaned_data["data"]

            # metatata fields
            metadata_fields = [
                c for c in f_data if c.lower() not in ["text", "label", "id", "id_hash"]
            ]
            # get list of items to dedup on
            if data.cleaned_data["dedup_on"] == "Text":
                dedup_metadata_fields = []
            elif data.cleaned_data["dedup_on"] == "Metadata_Text":
                dedup_metadata_fields = metadata_fields
            else:
                dedup_metadata_fields = [
                    d.strip()
                    for d in data.cleaned_data["dedup_fields"].strip().split(";")
                    if len(d.strip()) > 0
                ]

            for field in metadata_fields:
                use_with_dedup = False
                if field in dedup_metadata_fields:
                    use_with_dedup = True

                MetaDataField.objects.create(
                    project=proj_obj, field_name=field, use_with_dedup=use_with_dedup
                )

            add_queue(project=proj_obj, length=2000000, type="admin")
            irr_queue = add_queue(project=proj_obj, length=2000000, type="irr")
            upload_data(f_data, proj_obj, queue, irr_queue, batch_size)

        return HttpResponseRedirect(proj_obj.get_absolute_url())


class ProjectUpdateLanding(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "projects/update_landing.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Admin page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_context_data(self, **kwargs):
        ctx = super(ProjectUpdateLanding, self).get_context_data(**kwargs)

        ctx["project"] = Project.objects.get(pk=self.kwargs["pk"])

        return ctx


class ProjectUpdateOverview(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Project
    form_class = ProjectUpdateOverviewForm
    template_name = "projects/update/overview.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def form_valid(self, form):
        context = self.get_context_data()
        if form.is_valid():
            with transaction.atomic():
                self.object = form.save()
                return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectUpdateAdvanced(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Project
    form_class = ProjectUpdateAdvancedForm
    template_name = "projects/update/advanced.html"
    permission_denied_message = "You must be an Admin or Project Creator to access the Advanced Project Settings Update page."
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_form_kwargs(self):
        # pass the percentage_irr to the form
        kwargs = super().get_form_kwargs()
        project = self.get_object()
        kwargs["percentage_irr"] = project.percentage_irr
        return kwargs

    def form_valid(self, form):
        context = self.get_context_data()
        if form.is_valid():
            with transaction.atomic():
                self.object = form.save()
                return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectUpdateData(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Project
    form_class = DataUpdateWizardForm
    template_name = "projects/update/data.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_form_kwargs(self):
        form_kwargs = super(ProjectUpdateData, self).get_form_kwargs()

        form_kwargs["labels"] = list(
            Label.objects.filter(project=form_kwargs["instance"]).values_list(
                "name", flat=True
            )
        )

        form_kwargs["metadata"] = list(
            MetaDataField.objects.filter(project=form_kwargs["instance"]).values_list(
                "field_name", flat=True
            )
        )
        form_kwargs["dedup_on"] = form_kwargs["instance"].dedup_on
        form_kwargs["dedup_fields"] = form_kwargs["instance"].dedup_fields

        del form_kwargs["instance"]

        return form_kwargs

    def get_context_data(self, **kwargs):
        data = super(ProjectUpdateData, self).get_context_data(**kwargs)

        data["num_data"] = Data.objects.filter(project=data["project"]).count()

        return data

    def form_valid(self, form):
        context = self.get_context_data()

        if form.is_valid():
            with transaction.atomic():
                f_data = form.cleaned_data.get("data", False)
                if isinstance(f_data, pd.DataFrame):
                    queue = self.object.queue_set.get(type="normal")
                    irr_queue = self.object.queue_set.get(type="irr")
                    upload_data(
                        f_data,
                        self.object,
                        queue,
                        irr_queue,
                        batch_size=self.object.batch_size,
                    )

                return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectUpdateCodebook(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Project
    form_class = CodeBookWizardForm
    template_name = "projects/update/codebook.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_form_kwargs(self):
        form_kwargs = super(ProjectUpdateCodebook, self).get_form_kwargs()

        del form_kwargs["instance"]

        return form_kwargs

    def form_valid(self, form):
        context = self.get_context_data()
        if form.is_valid():
            with transaction.atomic():
                cb_data = form.cleaned_data.get("data", False)
                if cb_data and cb_data != "":
                    cb_filepath = save_codebook_file(cb_data, self.object.pk)
                    self.object.codebook_file = cb_filepath
                    self.object.save()

                return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectUpdateExternalDB(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = ExternalDatabase
    form_class = ExternalDatabaseWizardForm
    template_name = "projects/update/external_db.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_form_kwargs(self):
        form_kwargs = super(ProjectUpdateExternalDB, self).get_form_kwargs()
        return form_kwargs

    def get_success_url(self):
        context = self.get_context_data()
        return context["project"].get_absolute_url()

    def get_context_data(self, **kwargs):
        context = super(ProjectUpdateExternalDB, self).get_context_data(**kwargs)
        context["project"] = ExternalDatabase.objects.get(pk=self.kwargs["pk"]).project
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        project = context["project"]
        external_db = ExternalDatabase.objects.filter(project=project)
        if form.is_valid():
            with transaction.atomic():
                external_data = form.cleaned_data

                # either create or update existing external database connection
                if external_data["database_type"] != "none":
                    connection_dict = {
                        k: v
                        for k, v in external_data.items()
                        if k
                        in ["username", "password", "host", "port", "dbname", "driver"]
                    }
                    external_file = save_external_db_file(connection_dict, project.pk)

                    external_db.update(
                        env_file=external_file,
                        database_type=external_data["database_type"],
                        cron_ingest=external_data["cron_ingest"],
                        has_ingest=external_data["has_ingest"],
                        ingest_schema=external_data["ingest_schema"],
                        ingest_table_name=external_data["ingest_table_name"],
                        has_export=external_data["has_export"],
                        cron_export=external_data["cron_export"],
                        export_schema=external_data["export_schema"],
                        export_table_name=external_data["export_table_name"],
                        export_verified_only=external_data["export_verified_only"],
                    )
                elif project.has_database_connection():
                    # remove existing database connection
                    external_db.update(
                        env_file="",
                        database_type="none",
                        has_ingest=False,
                        ingest_schema=None,
                        cron_ingest=False,
                        ingest_table_name=None,
                        has_export=False,
                        cron_export=False,
                        export_schema=None,
                        export_table_name=None,
                    )

                    # delete credential file
                    delete_external_db_file(project.pk)

                return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectUpdateUmbrella(LoginRequiredMixin, UserPassesTestMixin, View):
    model = Project
    template_name = "projects/update/umbrella.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True
    ordering = "name"

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_context_data(self, **kwargs):
        context = {}
        project = Project.objects.get(pk=self.kwargs["pk"])
        context["project"] = project

        projects_umbrellas = get_projects_umbrellas(self)
        context["umbrella_choices"] = [
            umbrella_choice for umbrella_choice in projects_umbrellas if umbrella_choice
        ]

        context["umbrella"] = project.umbrella_string

        return context

    def get_success_url(self):
        context = self.get_context_data()
        return context["project"].get_absolute_url()

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        context = self.get_context_data()

        Project.objects.filter(id=context["project"].id).update(
            umbrella_string=request.POST.get("umbrella")
        )

        return redirect(self.get_success_url())


class ProjectUpdatePermissions(LoginRequiredMixin, UserPassesTestMixin, View):
    model = Project
    template_name = "projects/update/permissions.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_context_data(self, **kwargs):
        context = {}
        project = Project.objects.get(pk=self.kwargs["pk"])
        context["project"] = project

        if self.request.POST:
            context["permissions"] = PermissionsFormSet(
                self.request.POST,
                instance=project,
                prefix="permissions_set",
                form_kwargs={
                    "action": "update",
                    "creator": project.creator,
                    "profile": self.request.user.profile,
                },
            )
        else:
            context["permissions"] = PermissionsFormSet(
                instance=project,
                prefix="permissions_set",
                form_kwargs={
                    "action": "update",
                    "creator": project.creator,
                    "profile": self.request.user.profile,
                },
            )

        return context

    def get_success_url(self):
        context = self.get_context_data()
        return context["project"].get_absolute_url()

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        context = self.get_context_data()
        permissions = context["permissions"]
        if permissions.is_valid():
            with transaction.atomic():
                permissions.instance = context["project"]
                for deleted_permissions in permissions.deleted_forms:
                    del_perm_profile = deleted_permissions.cleaned_data.get(
                        "profile", None
                    )
                    batch_unassign(del_perm_profile)
                permissions.save()

                return redirect(self.get_success_url())
        else:
            return self.get(request)


class ProjectUpdateLabel(LoginRequiredMixin, UserPassesTestMixin, View):
    model = Project
    template_name = "projects/update/labels.html"
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Update page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )

    def get_context_data(self, **kwargs):
        context = {}
        project = Project.objects.get(pk=self.kwargs["pk"])
        context["project"] = project

        if self.request.POST:
            context["label_descriptions"] = LabelDescriptionFormSet(
                self.request.POST,
                instance=project,
                prefix="label_descriptions_set",
                form_kwargs={"action": "update"},
            )
        else:
            context["label_descriptions"] = LabelDescriptionFormSet(
                instance=project,
                prefix="label_descriptions_set",
                form_kwargs={"action": "update"},
            )
        return context

    def get_success_url(self):
        context = self.get_context_data()
        return context["project"].get_absolute_url()

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        context = self.get_context_data()
        labels = context["label_descriptions"]
        if labels.is_valid():
            with transaction.atomic():
                labels.instance = context["project"]
                labels.save()

            update_label_embeddings(context["project"])

            return redirect(self.get_success_url())
        else:
            return self.render_to_response(context)


class ProjectDelete(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Project
    template_name = "projects/confirm_delete.html"
    success_url = reverse_lazy("projects:project_list")
    permission_denied_message = (
        "You must be an Admin or Project Creator to access the Project Delete page."
    )
    raise_exception = True

    def test_func(self):
        project = Project.objects.get(pk=self.kwargs["pk"])

        return (
            project_extras.proj_permission_level(project, self.request.user.profile)
            >= 2
        )


class CreateFolder(LoginRequiredMixin, ListView):
    model = Project
    template_name = "projects/folder.html"

    def get_context_data(self, **kwargs):
        context = {}
        admin_or_created_projects = []

        # Only allow user to change folders of project they are the creator/have admin permissions
        for project in get_projects(self, False):
            if (
                project_extras.proj_permission_level(project, self.request.user.profile)
                >= 2
            ):
                admin_or_created_projects.append(project)
        admin_or_created_projects.sort(key=lambda x: x.name)
        context["projects"] = admin_or_created_projects

        if self.request.POST:
            context["project"] = self.request.POST["project"]
            context["umbrella"] = self.request.POST["umbrella"]

        return context

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, context=self.get_context_data())

    def post(self, request, *args, **kwargs):
        context = self.get_context_data()

        Project.objects.filter(id=context["project"]).update(
            umbrella_string=request.POST.get("umbrella")
        )

        return redirect("/projects")
