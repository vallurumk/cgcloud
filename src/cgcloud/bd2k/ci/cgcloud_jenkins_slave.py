from cgcloud.core.common_iam_policies import ec2_full_policy
from cgcloud.lib.util import abreviated_snake_case_class_name

from cgcloud.bd2k.ci.generic_jenkins_slaves import UbuntuTrustyGenericJenkinsSlave


class CgcloudJenkinsSlave( UbuntuTrustyGenericJenkinsSlave ):
    """
    A Jenkins slave for runing Cgcloud unit tests
    """

    @classmethod
    def recommended_instance_type( cls ):
        return "m3.xlarge"

    def _list_packages_to_install( self ):
        return super( CgcloudJenkinsSlave, self )._list_packages_to_install( ) + [
            # for PyCrypto:
            'python-dev',
            'autoconf',
            'automake',
            'binutils',
            'gcc',
            'make',
            'libyaml-dev',
            'libxml2-dev', 'libxslt-dev', 'zlib1g-dev'  # lxml
        ]

    def _get_iam_ec2_role( self ):
        role_name, policies = super( CgcloudJenkinsSlave, self )._get_iam_ec2_role( )
        role_name += '--' + abreviated_snake_case_class_name( CgcloudJenkinsSlave )
        cgcloud_bucket_arn = "arn:aws:s3:::%s" % self.ctx.s3_bucket_name
        policies.update( dict(
            ec2_full=ec2_full_policy,  # FIXME: Be more specific
            iam_cgcloud_jenkins_slave_pass_role=dict( Version="2012-10-17", Statement=[
                # This assumes that if instance lives in /, then tests running on the
                # instance will run in /test. If instance lives in /test, then tests
                # running on the instance will run in /test/test.
                dict( Effect="Allow",
                      Resource=self._role_arn( 'test/' ),
                      Action="iam:PassRole" ) ] ),
            s3_cgcloud_jenkins_slave=dict( Version="2012-10-17", Statement=[
                # for uploading keypair when creating a spark-box
                dict( Effect="Allow", Resource="arn:aws:s3:::*", Action="s3:ListAllMyBuckets" ),
                dict( Effect="Allow", Action="s3:*", Resource=[
                    cgcloud_bucket_arn,
                    cgcloud_bucket_arn + "/*" ] ) ] ),
            iam_cgcloud_jenkins_slave=dict( Version="2012-10-17", Statement=[
                dict( Effect="Allow", Resource="*", Action=[
                    "iam:CreateRole",
                    "iam:ListRolePolicies",
                    "iam:DeleteRolePolicy",
                    "iam:GetRolePolicy",
                    "iam:PutRolePolicy",
                    "iam:GetInstanceProfile",
                    "iam:CreateInstanceProfile",
                    "iam:RemoveRoleFromInstanceProfile",
                    "iam:AddRoleToInstanceProfile" ] ) ] ) ) )
        return role_name, policies
