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


class UsuarioEventoForm(forms.ModelForm):

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

    def save(self, commit=True):
        usuario = super().save(commit=False)

        email = self.cleaned_data["email"]

        # El correo será también el nombre de usuario.
        usuario.username = email
        usuario.email = email

        # La contraseña se establecerá mediante la invitación.
        usuario.set_unusable_password()

        usuario.is_active = True

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