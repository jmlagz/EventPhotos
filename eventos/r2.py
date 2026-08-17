import boto3
from django.conf import settings


def get_r2_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.R2_ENDPOINT,
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name=settings.R2_REGION,
    )


def generar_url_subida(object_key, content_type):
    r2 = get_r2_client()

    return r2.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": object_key,
            "ContentType": content_type,
        },
        ExpiresIn=300,
    )

def generar_url_lectura(object_key):
    r2 = get_r2_client()

    return r2.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.R2_BUCKET_NAME,
            "Key": object_key,
        },
        ExpiresIn=3600,
    )

def eliminar_objeto(object_key):
    r2 = get_r2_client()

    return r2.delete_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=object_key,
    )