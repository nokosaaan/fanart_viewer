from django.db import models


class Item(models.Model):
    external_id = models.IntegerField()
    # source identifies which JSON/data source this record came from (e.g. 'manosaba', 'mygo')
    source = models.CharField(max_length=64, blank=True, default='')
    situation = models.CharField(max_length=64, blank=True)
    titles = models.JSONField(default=list, blank=True)
    characters = models.JSONField(default=list, blank=True)
    artist = models.CharField(max_length=255, blank=True)
    link = models.URLField(blank=True)
    tags = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    preview_data = models.BinaryField(null=True, blank=True)
    preview_content_type = models.CharField(max_length=100, null=True, blank=True)

    def __str__(self):
        return f"{self.external_id} - {self.artist or 'unknown'}"


class PreviewImage(models.Model):
    item = models.ForeignKey(Item, related_name='preview_images', on_delete=models.CASCADE)
    order = models.IntegerField(default=0)
    data = models.BinaryField()
    content_type = models.CharField(max_length=100, blank=True, null=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"PreviewImage {self.item_id}#{self.order} ({self.content_type})"
