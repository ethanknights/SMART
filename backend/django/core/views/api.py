import csv
import io
import os
import tempfile
import zipfile

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.models import Project, IRRLog
from core.permissions import IsAdminOrCreator
from core.templatetags import project_extras
from core.utils.util import get_labeled_data
from core.utils.utils_external_db import export_table, load_ingest_table


@api_view(["GET"])
@permission_classes((IsAdminOrCreator,))
def download_data(request, project_pk, unverified):
    """This function gets the labeled data and makes it available for download.

    Args:
        request: The POST request
        project_pk: Primary key of the project
    Returns:
        an HttpResponse containing the requested data
    """
    project = Project.objects.get(pk=project_pk)
    data, labels = get_labeled_data(project, bool(int(unverified)))
    fieldnames = data.columns.values.tolist()
    data = data.to_dict("records")

    buffer = io.StringIO()
    wr = csv.DictWriter(buffer, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
    wr.writeheader()
    wr.writerows(data)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type="text/csv")
    response["Content-Disposition"] = "attachment;"

    return response


@api_view(["GET"])
@permission_classes((IsAdminOrCreator,))
def download_model(request, project_pk, unverified):
    """This function gets the labeled data and makes it available for download.

    Args:
        request: The POST request
        pk: Primary key of the project
    Returns:
        an HttpResponse containing the requested data
    """
    project = Project.objects.get(pk=project_pk)

    # https://stackoverflow.com/questions/12881294/django-create-a-zip-of-multiple-files-and-make-it-downloadable
    zip_subdir = "model_project" + str(project_pk)

    tfidf_path = os.path.join(
        settings.TF_IDF_PATH, "project_" + str(project_pk) + "_tfidf_matrix.pkl"
    )
    tfidf_vectorizer_path = os.path.join(
        settings.TF_IDF_PATH, "project_" + str(project_pk) + "_vectorizer.pkl"
    )
    readme_path = os.path.join(settings.BASE_DIR, "core", "data", "README.pdf")
    dockerfile_path = os.path.join(settings.BASE_DIR, "core", "data", "Dockerfile")
    requirements_path = os.path.join(
        settings.BASE_DIR, "core", "data", "requirements.txt"
    )
    start_script_path = os.path.join(
        settings.BASE_DIR, "core", "data", "start_notebook.sh"
    )
    usage_examples_path = os.path.join(
        settings.BASE_DIR, "core", "data", "UsageExamples.ipynb"
    )
    current_training_set = project.get_current_training_set()
    model_path = os.path.join(
        settings.MODEL_PICKLE_PATH,
        "project_"
        + str(project_pk)
        + "_training_"
        + str(current_training_set.set_number - 1)
        + ".pkl",
    )

    data, label_data = get_labeled_data(project, bool(int(unverified)))
    # open the tempfile and write the label data to it
    temp_labeleddata_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, dir=settings.DATA_DIR
    )
    temp_labeleddata_file.seek(0)
    data.to_csv(temp_labeleddata_file.name, index=False)
    temp_labeleddata_file.flush()
    temp_labeleddata_file.close()

    temp_label_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, dir=settings.DATA_DIR
    )
    temp_label_file.seek(0)
    label_data.to_csv(temp_label_file.name, index=False)
    temp_label_file.flush()
    temp_label_file.close()

    s = io.BytesIO()
    # open the zip folder
    zip_file = zipfile.ZipFile(s, "w")
    for path in [
        tfidf_path,
        tfidf_vectorizer_path,
        readme_path,
        model_path,
        temp_labeleddata_file.name,
        temp_label_file.name,
        dockerfile_path,
        requirements_path,
        start_script_path,
        usage_examples_path,
    ]:
        fdir, fname = os.path.split(path)
        if path == temp_label_file.name:
            fname = "project_" + str(project_pk) + "_labels.csv"
        elif path == temp_labeleddata_file.name:
            fname = "project_" + str(project_pk) + "_labeled_data.csv"
        # write the file to the zip folder
        zip_path = os.path.join(zip_subdir, fname)
        zip_file.write(path, zip_path)
    zip_file.close()

    response = HttpResponse(s.getvalue(), content_type="application/x-zip-compressed")
    response["Content-Disposition"] = "attachment;"

    return response


@api_view(["GET"])
@permission_classes((IsAdminOrCreator,))
def download_irr_log(request, project_pk):
    response = HttpResponse(
        content_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="irr_log_{project_pk}.csv"'
        },
    )

    writer = csv.writer(response)
    writer.writerow(["text", "label", "username", "timestamp"])

    logs = IRRLog.objects.filter(data__project_id=project_pk).select_related(
        "data", "profile", "label"
    )

    for log in logs:
        label_name = log.label.name if log.label else ""
        writer.writerow([log.data.text, label_name, log.profile.user, log.timestamp])

    return response


@api_view(["POST"])
@permission_classes((IsAdminOrCreator,))
def import_database_table(request, project_pk):
    """This function imports all data from an existing database connection.

    Args:
        request: The POST request
        project_pk: Primary key of the project
    Returns:
        {}
    """
    response = {}
    profile = request.user.profile
    project = Project.objects.get(pk=project_pk)

    # Make sure coder is an admin
    if project_extras.proj_permission_level(project, profile) > 1:
        response = load_ingest_table(project, response)
    else:
        response["error"] = "Invalid credentials. Must be an admin."

    if "error" in response.keys():
        return Response(response, status=status.HTTP_404_NOT_FOUND)

    return Response(response)


@api_view(["POST"])
@permission_classes((IsAdminOrCreator,))
def export_database_table(request, project_pk):
    """This function exports labeled data to an existing database connection.

    Args:
        request: The POST request
        project_pk: Primary key of the project
    Returns:
        {}
    """
    response = {}
    profile = request.user.profile
    # Make sure coder is an admin
    if (
        project_extras.proj_permission_level(
            Project.objects.get(pk=project_pk), profile
        )
        > 1
    ):
        export_table(project_pk, response)
    else:
        response["error"] = "Invalid credentials. Must be an admin."

    if "error" in response.keys():
        return Response(response, status=status.HTTP_404_NOT_FOUND)

    return Response(response)
