from django import forms
from django.contrib.auth.models import User

from .models import Evento


class EventoForm(forms.ModelForm):

    class Meta:
        model = Evento
        fields = [
            "nombre",
            "tipo",
            "fecha",
            "descripcion",
            "mensaje_bienvenida",
            "color_principal",
            "color_secundario",
            "plantilla",
            "permitir_videos",
            "moderacion_activa",
        ]

        widgets = {
            "fecha": forms.DateInput(
                attrs={
                    "type": "date",
                }
            ),
            "descripcion": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
            "mensaje_bienvenida": forms.Textarea(
                attrs={
                    "rows": 3,
                }
            ),
        }

class UsuarioForm(forms.ModelForm):

    ROL_ADMIN = "admin"
    ROL_ANFITRION = "anfitrion"

    rol = forms.ChoiceField(
        choices=[
            (ROL_ADMIN, "Administrador"),
            (ROL_ANFITRION, "Anfitrión"),
        ],
        widget=forms.RadioSelect,
        initial=ROL_ANFITRION,
    )

    eventos = forms.ModelMultipleChoiceField(
        queryset=Evento.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Eventos asignados",
    )

    class Meta:
        model = User
        fields = [
            "first_name",
            "last_name",
            "email",
        ]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(username=email).exists():
            raise forms.ValidationError(
                "Ya existe un usuario con este correo."
            )

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "Ya existe un usuario con este correo."
            )

        return email

    def clean(self):
        cleaned_data = super().clean()

        rol = cleaned_data.get("rol")
        eventos = cleaned_data.get("eventos")

        if rol == self.ROL_ANFITRION and not eventos:
            raise forms.ValidationError(
                "Debes asignar al menos un evento al anfitrión."
            )

        if rol == self.ROL_ADMIN:
            cleaned_data["eventos"] = Evento.objects.none()

        return cleaned_data

    def save(self, commit=True):
        usuario = super().save(commit=False)

        email = self.cleaned_data["email"]

        usuario.username = email
        usuario.email = email

        usuario.set_unusable_password()

        usuario.is_active = False

        if self.cleaned_data["rol"] == self.ROL_ADMIN:
            usuario.is_staff = True
            usuario.is_superuser = True
        else:
            usuario.is_staff = False
            usuario.is_superuser = False

        if commit:
            usuario.save()

        return usuario

class ActivarCuentaForm(forms.Form):

    password = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput,
        min_length=8,
    )

    password_confirmacion = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput,
        min_length=8,
    )

    def clean(self):
        cleaned_data = super().clean()

        password = cleaned_data.get("password")
        password_confirmacion = cleaned_data.get(
            "password_confirmacion"
        )

        if password and password_confirmacion:
            if password != password_confirmacion:
                raise forms.ValidationError(
                    "Las contraseñas no coinciden."
                )

        return cleaned_data